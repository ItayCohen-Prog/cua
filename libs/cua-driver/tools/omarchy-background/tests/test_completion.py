import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from background import TOOLS
from supervisor import finish_decision


class CompletionDecision(unittest.TestCase):
    def test_protocol_requires_choice_and_reason(self):
        schema=next(t for t in TOOLS if t['name']=='desktop_finish')['inputSchema']
        self.assertEqual(set(schema['required']),{'keep_open','reason'})
        self.assertNotIn('default',schema['properties']['keep_open'])

    def test_missing_or_invalid_decision_is_rejected(self):
        for arguments in ({}, {'keep_open':True}, {'reason':'Review'},
                          {'keep_open':'false','reason':'Done'},
                          {'keep_open':False,'reason':'  '}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                finish_decision(arguments)

    def test_both_contextual_choices_are_accepted(self):
        for choice, reason in [(True,'User wants to inspect the timeline.'),
                               (False,'The requested conversion is saved; the app is no longer needed.')]:
            self.assertEqual(finish_decision({'keep_open':choice,'reason':reason}),
                             {'keep_open':choice,'reason':reason})


if __name__=='__main__':unittest.main()
