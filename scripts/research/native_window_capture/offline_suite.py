"""Only synthetic controls; real process creation and OS signalling are forbidden."""
import importlib,os,signal,subprocess,unittest
from unittest.mock import patch

def main():
    signal.alarm(90)
    suite=unittest.TestSuite()
    with patch.object(subprocess,'Popen',side_effect=AssertionError('Real child forbidden')), patch.object(os,'kill',side_effect=AssertionError('Real signal forbidden')):
        for name in ('test_capture_fake','test_independent_fake','test_geometry_fake','test_consumer_fake'):
            module=importlib.import_module(name)
            for obj in vars(module).values():
                if isinstance(obj,type) and issubclass(obj,unittest.TestCase) and obj.__module__==name:
                    # Inherited baseline checks already run once in their defining class.
                    for method in sorted(n for n in obj.__dict__ if n.startswith('test_')):
                        suite.addTest(obj(method))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1
if __name__=='__main__':raise SystemExit(main())
