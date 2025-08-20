import unittest
import sys
print(sys.path)
import python_json_logger

class SimpleTest(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(1, 1)

if __name__ == '__main__':
    unittest.main()
