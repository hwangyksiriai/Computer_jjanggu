// Line-delimited JSON over private process pipes. No HTTP server, telemetry, or file upload.
import readline from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
const base=path.dirname(fileURLToPath(import.meta.url));
const config=path.join(base,'.local','runtime.json');
const runtime=fs.existsSync(config)?JSON.parse(fs.readFileSync(config,'utf8').replace(/^\uFEFF/,'' )).runtime_root:base;
const { pipeline, env, AutoProcessor, AutoTokenizer, CLIPVisionModelWithProjection,
        CLIPTextModelWithProjection, RawImage }=await import(pathToFileURL(path.join(runtime,'node_modules/@huggingface/transformers/dist/transformers.node.mjs')));
env.cacheDir=path.join(runtime===base?path.join(base,'.local'):runtime,'models');
env.allowRemoteModels=process.argv.includes('--setup');
env.backends.onnx.wasm.numThreads=2;
const options={device:'cpu',session_options:{intraOpNumThreads:2,interOpNumThreads:1}};
let encoder,vision,textEncoder,processor,tokenizer,generator,recognizer;
async function release(which){
  if(which==='chat'||which==='all') { if(generator) await generator.dispose(); generator=null; }
  if(which==='speech'||which==='all') { if(recognizer) await recognizer.dispose(); recognizer=null; }
  if(which==='search'||which==='all') {
    if(encoder) await encoder.dispose(); if(vision) await vision.dispose(); if(textEncoder) await textEncoder.dispose();
    encoder=null;vision=null;textEncoder=null;processor=null;tokenizer=null;
  }
  if(global.gc) global.gc();
}
async function embeddings(){
  await release('chat'); await release('speech');
  return encoder??=await pipeline('feature-extraction','Xenova/multilingual-e5-small',{...options,dtype:'q8'});
}
async function visual(){
  await release('chat'); await release('speech');
  const model='Xenova/clip-vit-base-patch32';
  processor??=await AutoProcessor.from_pretrained(model);
  tokenizer??=await AutoTokenizer.from_pretrained(model);
  vision??=await CLIPVisionModelWithProjection.from_pretrained(model,{...options,dtype:'q8'});
  textEncoder??=await CLIPTextModelWithProjection.from_pretrained(model,{...options,dtype:'q8'});
}
async function chat(){
  await release('search'); await release('speech');
  return generator??=await pipeline('text-generation','onnx-community/Qwen3-1.7B-ONNX',{...options,dtype:'q4'});
}
function normalized(tensor){
  const data=Array.from(tensor.data,Number),norm=Math.sqrt(data.reduce((a,v)=>a+v*v,0))||1;
  return data.map(v=>v/norm);
}
async function handle(r){
  if(r.op==='embed'){
    const e=await embeddings();
    const out=[];
    for(const value of r.texts){ const tensor=await e(value,{pooling:'mean',normalize:true,truncation:true,max_length:512}); out.push(Array.from(tensor.data)); }
    return out;
  }
  if(r.op==='image'){
    await visual(); const inputs=await processor(await RawImage.read(r.path));
    const output=await vision(inputs); return normalized(output.image_embeds);
  }
  if(r.op==='visual_text'){
    await visual(); const output=await textEncoder(tokenizer(r.text,{padding:true,truncation:true}));
    return normalized(output.text_embeds);
  }
  if(r.op==='chat'){
    const model=await chat();
    const result=await model(r.messages,{max_new_tokens:r.max_tokens??220,do_sample:false,
        tokenizer_kwargs:{enable_thinking:false},return_full_text:false});
    let value=result[0].generated_text;
    if(Array.isArray(value)) value=value.at(-1).content;
    return String(value).replace(/<think>[\s\S]*?<\/think>/g,'').trim();
  }
  if(r.op==='transcribe'){
    await release('search'); await release('chat');
    recognizer??=await pipeline('automatic-speech-recognition','Xenova/whisper-tiny',{...options,dtype:'q8'});
    return (await recognizer(Float32Array.from(r.audio),{language:'korean',task:'transcribe'})).text;
  }
  throw new Error('Unknown operation');
}
if(process.argv.includes('--setup')){
  for(const [name,fn] of [['semantic',embeddings],['vision',visual],['conversation',chat],
     ['speech',async()=>{recognizer=await pipeline('automatic-speech-recognition','Xenova/whisper-tiny',{...options,dtype:'q8'});} ]]){
    console.log(JSON.stringify({stage:name,status:'downloading'})); await fn();
    console.log(JSON.stringify({stage:name,status:'ready'})); await release('all');
  }
  console.log(JSON.stringify({setup:'complete'})); process.exit(0);
}else{
  for await(const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})){
    let r;
    try {r=JSON.parse(line); const value=await handle(r); console.log(JSON.stringify({id:r.id,ok:true,value}));}
    catch(e){console.log(JSON.stringify({id:r?.id,ok:false,error:String(e.message??e)}));}
  }
}
