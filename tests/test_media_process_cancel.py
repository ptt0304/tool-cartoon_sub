import sys
import threading
import time
import unittest
from threading import Event

from cartoon_sub.media.process import CancelledError, run_process


class MediaProcessCancelTests(unittest.TestCase):
    def test_cancel_terminates_only_owned_process_promptly(self):
        cancel = Event(); result = []
        def target():
            try:
                run_process([sys.executable, "-c", "import time; time.sleep(30)"], cancel=cancel)
            except Exception as exc:
                result.append(exc)
        thread = threading.Thread(target=target); thread.start(); time.sleep(.3); cancel.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], CancelledError)


if __name__ == "__main__": unittest.main()
