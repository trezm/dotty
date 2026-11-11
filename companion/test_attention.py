import json
import os
from pathlib import Path
import pty
import sqlite3
import tempfile
import unittest

from claude_desktop import DesktopClaude
from claude_hook import record
from claude_status import attention_snapshot, acknowledge_attention_snapshot
from codex_questions import PendingQuestions, QuestionStateError
from desktop_unread import DesktopUnread
from snapshot import save_snapshot
from test_desktop_unread import state, report
import test_watch


class QuestionTest(unittest.TestCase):
    def database(self, path):
        db = sqlite3.connect(path)
        db.executescript('''CREATE TABLE thread_turns(thread_id TEXT,turn_id TEXT,status TEXT,rollout_ordinal INT);
          CREATE TABLE thread_items(thread_id TEXT,turn_id TEXT,item_id TEXT,item_type TEXT,item_json TEXT);''')
        db.execute("INSERT INTO thread_turns VALUES ('one','turn','inProgress',1)")
        return db

    def question(self, db, call='q1', turn='turn'):
        db.execute("INSERT INTO thread_items VALUES ('one',?,?,?,?)", (turn,call,'agentMessage',
            json.dumps({'questions':[{'title':'PRIVATE QUESTION','options':[]}]})))
        db.commit()

    def answer(self, db, call='q1'):
        text='<send_user_message_question_reply>\n'+json.dumps([{
            'questionItemId':json.dumps(['request_user_input_async',call,0]),'answer':'PRIVATE ANSWER'}])+\
            '\n</send_user_message_question_reply>\n'
        db.execute("INSERT INTO thread_items VALUES ('one','turn','reply','userMessage',?)", (
            json.dumps({'content':[{'type':'text','text':text}]}),))
        db.commit()

    def test_question_answer_and_old_turns_are_not_stale_notifications(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'history.sqlite';db=self.database(path);self.question(db)
            q=PendingQuestions(path)
            self.assertEqual(set(q.pending(['one'])),{'one'})
            self.assertNotIn('PRIVATE',json.dumps(q.pending(['one'])))
            self.answer(db);self.assertEqual(q.pending(['one']),{})
            self.question(db,'q2')
            db.execute("INSERT INTO thread_turns VALUES ('one','new','inProgress',2)");db.commit()
            self.assertEqual(q.pending(['one']),{})
            self.question(db,'q3','new');self.assertEqual(set(q.pending(['one'])),{'one'})
            db.execute("UPDATE thread_turns SET status='completed' WHERE turn_id='new'");db.commit()
            self.assertEqual(q.pending(['one']),{})  # Optional old cards are not permanent alerts.
            db.execute("UPDATE thread_turns SET status='interrupted' WHERE turn_id='new'");db.commit()
            self.assertEqual(q.pending(['one']),{})
            db.close()
            with self.assertRaises(QuestionStateError):PendingQuestions(Path(td)/'missing.sqlite').pending(['one'])
            self.assertFalse((Path(td)/'missing.sqlite').exists())

    def test_pending_questions_count_without_an_unread_flag_and_realert_after_hold(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);path=root/'history.sqlite';db=self.database(path);self.question(db)
            source=root/'desktop.json';save_snapshot(state(),source);before=source.read_bytes()
            adapter=DesktopUnread(source,root/'ack.json',PendingQuestions(path))
            current=report(native=False)
            self.assertEqual(adapter.snapshot(current)['unread_count'],1)
            seen=tuple(adapter.snapshot(current)['unread_revisions'].items())
            self.assertEqual(adapter.acknowledge(seen,current),(1,0))
            self.assertEqual(adapter.snapshot(current)['unread_count'],0)
            self.question(db,'q2')
            self.assertEqual(adapter.snapshot(current)['unread_count'],1)
            self.assertEqual(adapter.acknowledge(seen,current),(0,1))
            self.answer(db);self.answer(db,'q2')
            self.assertEqual(adapter.snapshot(current)['unread_count'],0)
            self.assertEqual(source.read_bytes(),before)
            db.close()


class ClaudeDesktopTest(unittest.TestCase):
    account='11111111-1111-1111-1111-111111111111'
    org='22222222-2222-2222-2222-222222222222'
    cli='33333333-3333-3333-3333-333333333333'
    ident='local_'+cli

    def fixture(self, root, **updates):
        profile=root/'Claude';directory=profile/'claude-code-sessions'/self.account/self.org
        directory.mkdir(parents=True,exist_ok=True)
        save_snapshot({'lastKnownAccountUuid':self.account},profile/'config.json')
        row={'sessionId':self.ident,'cliSessionId':self.cli,'isArchived':False,
             'lastFocusedAt':900,'latestUserFrameAt':800,'lastTurnEdgeAt':1000,
             'title':'PRIVATE TITLE','turnWrapUp':{'description':'PRIVATE DESCRIPTION'}}
        row.update(updates);path=directory/'session.json';save_snapshot(row,path)
        return DesktopClaude(profile,root/'runtime'/'claude-desktop-ack.json'),path,row

    def test_native_completion_read_and_waiting_marker_resolution(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);adapter,path,row=self.fixture(root)
            self.assertEqual(len(adapter.pending()),1)
            row['lastFocusedAt']=1100;save_snapshot(row,path)
            self.assertEqual(adapter.pending(force=True),{})
            row['turnWrapUp']['needsUser']='PRIVATE QUESTION';save_snapshot(row,path)
            pending=adapter.pending(force=True)
            self.assertEqual(next(iter(pending.values()))['kind'],'question')
            self.assertNotIn('PRIVATE',json.dumps(pending))
            row['latestUserFrameAt']=1200;save_snapshot(row,path)
            self.assertEqual(adapter.pending(force=True),{})
            row['lastTurnEdgeAt']=1300;row['isArchived']=True;save_snapshot(row,path)
            self.assertEqual(adapter.pending(force=True),{})

    def test_clear_is_local_revision_guarded_and_deduplicates_hooks(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);adapter,path,row=self.fixture(root)
            events=root/'events.json'
            record({'session_id':self.cli,'hook_event_name':'Stop'},events,{},now=1)
            seen=attention_snapshot(events,adapter)
            self.assertEqual(len(seen),1)
            self.assertNotIn('PRIVATE',json.dumps(seen))
            before=path.read_bytes()
            acknowledge_attention_snapshot(seen,events,adapter)
            self.assertEqual(attention_snapshot(events,adapter),{})
            self.assertEqual(path.read_bytes(),before)
            row['lastTurnEdgeAt']=1400;save_snapshot(row,path);adapter.next_scan=0
            self.assertEqual(len(attention_snapshot(events,adapter)),1)
            self.assertEqual(adapter.acknowledge(seen),(0,1))
            # Terminal sessions retain the existing hook path.
            record({'session_id':'terminal','hook_event_name':'Stop'},events,{'TERM_PROGRAM':'Apple_Terminal'})
            self.assertEqual(len(attention_snapshot(events,adapter)),2)

    def test_changed_schema_is_unknown_and_never_overwrites_claude(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);adapter,path,row=self.fixture(root)
            row['lastTurnEdgeAt']='unknown';save_snapshot(row,path);before=path.read_bytes()
            self.assertIsNone(adapter.pending(force=True))
            self.assertEqual(path.read_bytes(),before)
            self.assertFalse(adapter.journal.exists())


class AttentionWatchTest(unittest.TestCase):
    def test_both_native_sources_reach_the_real_entrypoint_and_answer_receipts_clear_them(self):
        helper=test_watch.WatchTest();question=QuestionTest();native=ClaudeDesktopTest()
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);binary,_=helper.fake_server(root)
            history=root/'history.sqlite';db=question.database(history)
            source=root/'desktop.json';save_snapshot(state(),source)
            adapter,path,row=native.fixture(root,lastFocusedAt=1100)
            master,slave=pty.openpty()
            process=helper.start(root,binary,os.ttyname(slave),extra=(
                '--desktop-state',str(source),'--codex-question-state',str(history),
                '--claude-desktop-profile',str(adapter.profile)))
            try:
                helper.frame(master,[b'0',b'0',b'0',b'5',b'5'])
                question.question(db)
                helper.frame(master,[b'1',b'0',b'0',b'5',b'5'])
                row['lastTurnEdgeAt']=1200;row['turnWrapUp']['needsUser']='PRIVATE QUESTION'
                save_snapshot(row,path)
                helper.frame(master,[b'1',b'0',b'1',b'5',b'5'])
                published=root/'published-status.json'
                helper.until(lambda: published.exists() and json.loads(published.read_text())['claude_count']==1)
                self.assertEqual(json.loads(published.read_text())['codex_count'],1)
                question.answer(db)
                helper.frame(master,[b'0',b'0',b'1',b'5',b'5'])
                row['latestUserFrameAt']=1300;save_snapshot(row,path)
                helper.frame(master,[b'0',b'0',b'0',b'5',b'5'])
            finally:
                output=helper.stop(process)
                self.assertNotIn('PRIVATE',output)
                db.close();os.close(master);os.close(slave)


if __name__=='__main__':unittest.main()
