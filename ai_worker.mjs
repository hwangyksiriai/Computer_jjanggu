// Line-delimited JSON over private process pipes. No HTTP server, telemetry, or file upload.
import readline from 'node:readline';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
const base=path.dirname(fileURLToPath(import.meta.url));
const state=process.env.JJANGGU_STATE_ROOT||path.join(base,'.local');
const config=path.join(state,'runtime.json');
let configuredRuntime;
try {const value=JSON.parse(fs.readFileSync(config,'utf8').replace(/^\uFEFF/,''))?.runtime_root;
  if(typeof value==='string'&&path.isAbsolute(value))configuredRuntime=value;} catch {}
const runtime=process.env.JJANGGU_RUNTIME_ROOT||configuredRuntime||base;
const modules=process.env.JJANGGU_NODE_MODULES||path.join(runtime,'node_modules');
const { pipeline, env, AutoProcessor, AutoTokenizer, CLIPVisionModelWithProjection,
        CLIPTextModelWithProjection, RawImage }=await import(pathToFileURL(path.join(modules,'@huggingface/transformers/dist/transformers.node.mjs')));
env.cacheDir=path.join(runtime===base?state:runtime,'models');
const setupFlag=process.argv.find(arg=>arg==='--setup'||arg.startsWith('--setup-'));
env.allowRemoteModels=Boolean(setupFlag);
env.backends.onnx.wasm.numThreads=2;
let setupStage='',lastDownloadNotice=0;
function downloadProgress(event){
  if(!env.allowRemoteModels||event.status!=='progress'||Date.now()-lastDownloadNotice<750)return;
  lastDownloadNotice=Date.now();
  console.log(JSON.stringify({stage:setupStage,status:'progress',percent:Math.round(event.progress||0),loaded:event.loaded,total:event.total}));
}
const options={device:'cpu',session_options:{intraOpNumThreads:2,interOpNumThreads:1},progress_callback:downloadProgress};
let encoder,vision,textEncoder,processor,tokenizer,generator,recognizer,detector;
async function release(which){
  if(which==='chat'||which==='all') { if(generator) await generator.dispose(); generator=null; }
  if(which==='speech'||which==='all') { if(recognizer) await recognizer.dispose(); recognizer=null; }
  if(which==='search'||which==='all') {
    if(encoder) await encoder.dispose(); if(vision) await vision.dispose(); if(textEncoder) await textEncoder.dispose();
    encoder=null;vision=null;textEncoder=null;processor=null;tokenizer=null;
    if(detector) await detector.dispose(); detector=null;
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
async function objects(){
  await release('chat'); await release('speech');
  return detector??=await pipeline('object-detection','Xenova/detr-resnet-50',{...options,dtype:'q8'});
}
function speechModel(){
  const small=path.join(env.cacheDir,'Xenova/whisper-small/onnx');
  const complete=['encoder_model_quantized.onnx','decoder_model_merged_quantized.onnx'].every(name=>{
    try{return fs.statSync(path.join(small,name)).size>1024*1024;}catch{return false;}
  });
  return env.allowRemoteModels||complete?'Xenova/whisper-small':'Xenova/whisper-tiny';
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
  if(r.op==='objects'){
    const model=await objects();
    return await model(r.path,{threshold:0.65,percentage:true});
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
    recognizer??=await pipeline('automatic-speech-recognition',speechModel(),{...options,dtype:'q8'});
    return (await recognizer(Float32Array.from(r.audio),{language:'korean',task:'transcribe'})).text;
  }
  throw new Error('Unknown operation');
}
if(setupFlag){
  const selected={'--setup-photo':['vision','objects'],'--setup-semantic':['semantic'],'--setup-vision':['vision'],
    '--setup-objects':['objects'],'--setup-chat':['conversation'],'--setup-speech':['speech'],'--setup-search':['semantic','vision']};
  for(const [name,fn] of [['semantic',embeddings],['vision',visual],['objects',objects],['conversation',chat],
     ['speech',async()=>{recognizer=await pipeline('automatic-speech-recognition',speechModel(),{...options,dtype:'q8'});} ]]){
    if(selected[setupFlag]&&!selected[setupFlag].includes(name))continue;
    setupStage=name;console.log(JSON.stringify({stage:name,status:'downloading'})); await fn();
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
