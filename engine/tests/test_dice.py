import random
import unittest

from engine.dice import Dice, SequenceSource


class DiceTests(unittest.TestCase):
    def test_sequence_source_is_controlled_and_ignores_global_random(self):
        def fail_global(*_args, **_kwargs):
            raise AssertionError("global random was used")

        original = random.randint
        random.randint = fail_global
        try:
            source = SequenceSource([4, 19])
            dice = Dice(source)
            self.assertEqual(dice.roll(20), 4)
            self.assertEqual(dice.roll(20), 19)
            self.assertEqual(source.consumed, 2)
        finally:
            random.randint = original

    def test_sequence_source_rejects_values_outside_the_die(self):
        dice = Dice(SequenceSource([0]))
        with self.assertRaises(RuntimeError):
            dice.roll(20)

    def test_exhausted_source_raises(self):
        dice = Dice(SequenceSource([2]))
        dice.roll(6)
        with self.assertRaises(RuntimeError):
            dice.roll(6)


if __name__ == "__main__":
    unittest.main()
