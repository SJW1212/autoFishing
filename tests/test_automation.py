import unittest
from automation import DEFAULT_TARGET, FishingController, FishingOptions, matches_subtitle, within_one_edit


class MatchingTests(unittest.TestCase):
    def test_exact_and_normalized(self):
        for text in ("낚시찌가 첨벙거림", "< 낚시 찌가 첨벙 거림 >", "발소리\n낚시찌가 첨벙거림 >"):
            self.assertTrue(matches_subtitle(text, DEFAULT_TARGET))

    def test_ocr_single_error(self):
        self.assertTrue(matches_subtitle("낚시찌가 점벙거림", DEFAULT_TARGET))
        self.assertTrue(matches_subtitle("낚시찌가첨벙거림 >", DEFAULT_TARGET))
        self.assertTrue(matches_subtitle("낚시찌가 침범거림", DEFAULT_TARGET))
        self.assertTrue(matches_subtitle("낚시찌가 점범거림", DEFAULT_TARGET))

    def test_other_sounds_do_not_trigger(self):
        for text in ("무언가가 튀는 소리", "낚시찌를 던짐", "낚시찌를 회수함", "돌고래가 첨벙거림",
                     "아홀로틀이 첨벙거림", "무언가가 첨벙거리는 소리", "", "물 흐르는 소리"):
            self.assertFalse(matches_subtitle(text, DEFAULT_TARGET), text)

    def test_invalid_target(self):
        self.assertFalse(matches_subtitle("any text", ""))
        self.assertFalse(matches_subtitle("abc", "abc"))

    def test_edit_boundary(self):
        self.assertTrue(within_one_edit("abc", "abcd"))
        self.assertTrue(within_one_edit("abcd", "abc"))
        self.assertFalse(within_one_edit("abxy", "abcd"))


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.clicks = []
        self.messages = []
        self.focused = True
        self.controller = FishingController(lambda: self.clicks.append(1), lambda: self.focused, self.messages.append)

    def begin(self, already_cast=False):
        self.controller.start(0, FishingOptions(already_cast=already_cast), .5)
        self.controller.tick(.5, True)

    def arm(self, start=1):
        self.controller.observe(start, False, start)
        self.controller.observe(start + .5, False, start + .5)

    def test_first_cast_and_one_catch(self):
        self.begin()
        self.arm()
        self.controller.observe(3.1, True, 3.1)
        self.assertEqual(len(self.clicks), 2)
        self.assertEqual(self.controller.count, 1)
        self.controller.tick(4, True)
        self.assertEqual(len(self.clicks), 3)

    def test_lingering_subtitle_does_not_retrigger(self):
        self.test_first_cast_and_one_catch()
        for moment in (4.2, 5, 6, 7, 8):
            self.controller.observe(moment, True, moment)
            self.controller.tick(moment, True)
        self.assertEqual(len(self.clicks), 3)
        self.arm(9)
        self.controller.observe(10, True, 10)
        self.assertEqual(self.controller.count, 2)

    def test_stale_subtitle_at_start_does_not_catch(self):
        self.begin(already_cast=True)
        self.controller.observe(4, True, 4)
        self.assertEqual(self.clicks, [])

    def test_settle_period(self):
        self.begin()
        self.arm()
        self.controller.observe(2, True, 2)
        self.assertEqual(len(self.clicks), 1)

    def test_focus_loss_stops_before_recast(self):
        self.test_first_cast_and_one_catch()
        self.arm(5)
        self.controller.observe(7, True, 7)
        before = len(self.clicks)
        self.focused = False
        self.controller.tick(8, True)
        self.assertFalse(self.controller.running)
        self.assertEqual(len(self.clicks), before)

    def test_stopped_has_no_delayed_click(self):
        self.begin()
        self.controller.stop()
        self.controller.tick(100, True)
        self.controller.observe(100, True, 100)
        self.assertEqual(len(self.clicks), 1)

    def test_screen_failure_stops(self):
        self.begin()
        self.controller.tick(3, False)
        self.assertFalse(self.controller.running)

    def test_timeout_stops_without_blind_click(self):
        self.begin()
        self.controller.tick(66, True)
        self.assertFalse(self.controller.running)
        self.assertEqual(len(self.clicks), 1)

    def test_old_frame_cannot_arm(self):
        self.begin()
        self.controller.observe(3, False, .1)
        self.controller.observe(4, False, .2)
        self.assertFalse(self.controller.armed)

    def test_invalid_timings(self):
        for value in (0, .1, 4):
            with self.assertRaises(ValueError):
                FishingOptions(recast_delay=value).validate()


if __name__ == "__main__":
    unittest.main()
