"""Frozen literal geometry controls; no native operations, all processes/decoders are fakes."""
import copy,inspect,math
from pathlib import Path
import ownedleaf as o,capture as c
from test_capture_fake import Controls,Fake,png
class Geometry(Controls):
 def configure_geometry(self,points,pixels):
  self.record['bounds'].update(width=points[0],height=points[1])
  def spawn(argv,**kwargs):
   self.spawn_calls+=1;Path(argv[-1]).write_bytes(png(pixels[0],pixels[1]));return self.child
  def decode(path):self.decoded+=1;return {'width':pixels[0],'height':pixels[1]}
  self.leaves.spawn=spawn;self.decode=decode
 def test_G_A1200x700_native2400x1400(self):
  self.configure_geometry((1200,700),(2400,1400));r=self.capture(expected_geometry=(1200,700));self.assertEqual(r['scale'],2);self.assertEqual((r['image']['width'],r['image']['height']),(2400,1400))
 def test_G_B640x420_native1280x840(self):
  self.configure_geometry((640,420),(1280,840));r=self.capture(expected_geometry=(640,420));self.assertEqual(r['scale'],2);self.assertEqual((r['image']['width'],r['image']['height']),(1280,840))
 def test_G_1512x982_native3024x1964_fake_only(self):
  self.configure_geometry((1512,982),(3024,1964));r=self.capture(expected_geometry=[1512,982]);self.assertEqual(r['scale'],2);self.assertEqual((r['image']['width'],r['image']['height']),(3024,1964));self.assertTrue(r['image']['full_raster_validated'])
 def test_G_missing_argument_has_no_default_or_callbacks(self):
  p=inspect.signature(c.capture_once).parameters['expected_geometry'];self.assertEqual(p.kind,inspect.Parameter.KEYWORD_ONLY);self.assertIs(p.default,inspect.Parameter.empty)
  def forbidden(*a):raise AssertionError('Unexpected callback')
  with self.assertRaises(TypeError):c.capture_once(self.expected,77,self.root/'case',forbidden,forbidden,leaves=self.leaves,image_inspect=forbidden)
  self.assertEqual(self.spawn_calls,0);self.assertEqual(self.l.actual_captures,0)
 def test_G_invalid_geometry_before_callbacks_and_spawn(self):
  values=[None,(),(1,), (1,2,3),'1200x700',(True,700),(1200,False),(1200.0,700),(float('nan'),700),(float('inf'),700),(0,700),(-1,700),(4097,700)]
  def forbidden(*a):raise AssertionError('Unexpected callback')
  for value in values:
   with self.subTest(value=repr(value)):
    with self.assertRaises(o.Refused):c.capture_once(self.expected,77,self.root/'case',forbidden,forbidden,expected_geometry=value,leaves=self.leaves,image_inspect=forbidden)
  self.assertEqual(self.spawn_calls,0);self.assertEqual(self.l.actual_captures,0);self.assertFalse((self.root/'case').exists())
 def test_G_expected1512_actual1200_refuses_before_capture(self):
  self.refused_capture(expected_geometry=(1512,982));self.assertEqual(self.spawn_calls,0);self.assertEqual(self.l.actual_captures,0)
 def test_G_expected1200_actual1512_refuses_before_capture(self):
  self.record['bounds'].update(width=1512,height=982);self.refused_capture(expected_geometry=(1200,700));self.assertEqual(self.spawn_calls,0);self.assertEqual(self.l.actual_captures,0)
 def test_G_post_position_change_disposes_without_decode(self):
  changed=copy.deepcopy(self.record);changed['bounds']['x']=1;values=iter([self.record,changed]);self.refused_capture(window_inspect=lambda w:next(values));self.assertEqual(self.decoded,0);self.assertFalse((self.root/'case/window.png').exists());self.assertEqual(self.l.attempts[0]['disposition'],'exact-owned-suspect-deleted')
 def test_G_fractional_backing_refused(self):
  self.configure_geometry((1512,982),(2268,1473));self.refused_capture(expected_geometry=(1512,982));self.assertEqual(self.decoded,0)
 def test_G_nonuniform_backing_refused(self):
  self.configure_geometry((1512,982),(3024,1965));self.refused_capture(expected_geometry=(1512,982));self.assertEqual(self.decoded,0)
 def test_G_pixel4097_refused(self):
  self.configure_geometry((1512,982),(4097,1));self.refused_capture(expected_geometry=(1512,982));self.assertEqual(self.decoded,0)
