import unittest
from unittest.mock import patch
import agent, insights

class DateRangeTests(unittest.TestCase):
    def test_analysis_queries_historical_bounds_and_never_includes_other_dates(self):
        class Reader:
            def sessions(self, limit):
                return [{'sortAt': 500, 'category':'direct', 'username':'a', 'display':'A', 'unread':0}]
            def messages(self, username, limit, start=None, end=None):
                self.bounds = (start, end)
                return [{'at':150,'text':'报价','senderId':'other','sender':'A','serverId':'1','timeText':'1970-01-01 00:02:30'}]
        r=Reader()
        corpus, index, _ = agent.build_corpus(r,'self',5,100,200)
        self.assertEqual(r.bounds,(100,200))
        self.assertIn('报价', corpus)
        self.assertEqual(len(index),1)
        insights.build_digest(r,'self','',start=100,end=200)
        self.assertEqual(r.bounds,(100,200))

    def test_corpus_evidence_never_references_truncated_message(self):
        class Reader:
            def sessions(self, limit):
                return [{'sortAt':500,'category':'direct','username':'a','display':'A','unread':0}]
            def messages(self, username, limit, start=None, end=None):
                return [{'at':150,'text':'x'*200,'senderId':'a','sender':'A','serverId':str(i),'timeText':'t'} for i in range(4)]
        with patch.object(agent,'MAX_TOTAL_CHARS',300):
            corpus,index,_=agent.build_corpus(Reader(),'self',5,100,200)
        for key in index:
            self.assertIn('['+key+']',corpus)

if __name__ == '__main__': unittest.main()
