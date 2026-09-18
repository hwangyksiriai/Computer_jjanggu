import unittest
from monitors import fit_position, virtual_bounds

class MonitorTests(unittest.TestCase):
    def test_right_monitor(self):
        self.assertEqual(fit_position(2200,500,270,294,[(0,0,1920,1080),(1920,0,3840,1080)]),(2200,500))
    def test_left_negative_origin(self):
        self.assertEqual(fit_position(-1500,500,270,294,[(-1920,0,0,1080),(0,0,1920,1080)]),(-1500,500))
    def test_upper_negative_origin(self):
        self.assertEqual(fit_position(400,-900,270,294,[(0,-1080,1920,0),(0,0,1920,1080)]),(400,-900))
    def test_unplugged_monitor_recovers(self):
        self.assertEqual(fit_position(-1600,600,270,294,[(0,0,1920,1080)]),(0,600))
    def test_gap_between_uneven_displays(self):
        screens=[(0,0,1920,1080),(1920,400,3840,1480)]
        self.assertEqual(fit_position(2400,100,270,294,screens),(2400,400))
    def test_fit_after_size_increase(self):
        self.assertEqual(fit_position(1780,950,310,374,[(0,0,1920,1080)]),(1610,706))
    def test_combined_bounds_do_not_clamp_to_primary(self):
        self.assertEqual(virtual_bounds([(-1920,-400,0,680),(0,0,2560,1440)]),(-1920,-400,2560,1440))
    def test_boundary_drop_is_visible(self):
        self.assertEqual(fit_position(1800,500,270,294,[(0,0,1920,1080),(1920,0,3840,1080)]),(1920,500))

if __name__=='__main__': unittest.main(verbosity=2)
