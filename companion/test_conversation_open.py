import argparse
import json
import os
from pathlib import Path
import pty
import queue
import select
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from conversation_open import (Destination, TapNavigator, claude_destinations,
                               codex_destinations, open_destination)
from dotty_companion import read_snapshot, run
from snapshot import save_snapshot

LOCAL_A = 'local_11111111-1111-4111-8111-111111111111'
LOCAL_B = 'local_22222222-2222-4222-8222-222222222222'


def notice(key, *, urgent=False, at=1, receipt='r'):
    return Destination(key, receipt, 'openai', 'codex://threads/'+key, urgent, at)


class ConversationOpenTest(unittest.TestCase):
    def test_priority_cycle_per_provider_new_receipts_and_removed_notices(self):
        nav=TapNavigator()
        waiting,reply=notice('question',urgent=True,at=20),notice('reply',at=1)
        candidates=(reply,waiting)
        self.assertEqual(nav.choose(0,candidates,candidates),waiting)
        nav.opened(0,waiting)
        self.assertEqual(nav.choose(0,candidates,candidates),reply)
        self.assertEqual(nav.choose(1,candidates,candidates),waiting)
        nav.opened(0,reply)
        self.assertEqual(nav.choose(0,candidates,candidates),waiting)  # Wrap.
        nav.opened(0,waiting)
        changed=notice('question',urgent=True,receipt='new')
        self.assertEqual(nav.choose(0,(reply,changed),(reply,changed)),changed)
        self.assertIsNone(nav.choose(0,(waiting,),(changed,)))  # Stale screen.
        self.assertEqual(nav.choose(0,(reply,waiting),(reply,)),reply)
        self.assertIsNone(nav.choose(0,candidates,()))

    def test_codex_ids_are_receipt_matched_fresh_and_never_executable_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot.json'
            rows=[{'kind':'codex','id':'reply','readRevision':'r','activityAt':2},
                  {'kind':'codex','id':'question','readRevision':'q','attention':'question'}]
            raw={'version':1,'generated_at':100,'available':{'codex':True,'chatgpt':False},'unread':rows}
            save_snapshot(raw,path)
            receipts=(('reply','r'),('question','q'))
            got=codex_destinations(path,10,receipts,now=101)
            self.assertEqual({d.url for d in got},{'codex://threads/reply','codex://threads/question'})
            self.assertTrue(next(d for d in got if d.key=='question').urgent)
            self.assertEqual(codex_destinations(path,10,receipts,now=111),())
            self.assertEqual(codex_destinations(path,10,(('reply','changed'),),now=101),())
            for ident in ('new','NEW','../settings','foo?prompt=send','x;touch /tmp/no','https://evil','%2fnew'):
                raw['unread']=[{'kind':'codex','id':ident,'readRevision':'r'}]
                save_snapshot(raw,path)
                self.assertEqual(codex_destinations(path,10,((ident,'r'),),now=101),())
            raw['available']['codex']=False
            save_snapshot(raw,path)
            self.assertEqual(codex_destinations(path,10,((ident,'r'),),now=101),())

    def test_claude_local_route_aliases_and_terminal_fallback(self):
        rows={'desktop:'+LOCAL_A:{'target':'claude','kind':'question','at':2},
              'cli-id':{'target':'claude','kind':'completion','at':3},
              'terminal':{'target':'terminal','kind':'permission','at':1}}
        got=claude_destinations(rows,{'cli-id':'desktop:'+LOCAL_A})
        self.assertEqual(len(got),2)
        local=next(d for d in got if d.target=='claude')
        self.assertEqual(local.url,'claude://code/continue?session='+LOCAL_A)
        self.assertTrue(local.urgent)
        self.assertIsNone(next(d for d in got if d.target=='terminal').url)
        self.assertIsNone(claude_destinations({'raw-cli-uuid':{'target':'claude','at':1}})[0].url)
        self.assertIsNone(claude_destinations({'desktop:local_bad?prompt=send':{'target':'claude','at':1}})[0].url)
        self.assertEqual(claude_destinations(None),())

    def test_dispatch_uses_fixed_arguments_falls_back_and_dry_run_does_nothing(self):
        d=notice('question',urgent=True)
        with patch('conversation_open.subprocess.run',return_value=subprocess.CompletedProcess([],0)) as run_open:
            self.assertEqual(open_destination(d,0),('openai',True,'conversation'))
            self.assertEqual(run_open.call_args.args[0],['/usr/bin/open','-b','com.openai.codex','codex://threads/question'])
            run_open.reset_mock()
            self.assertEqual(open_destination(d,0,dry_run=True),('openai',False,'conversation'))
            run_open.assert_not_called()
        with patch('conversation_open.subprocess.run',side_effect=[subprocess.TimeoutExpired('open',2),subprocess.CompletedProcess([],0)]) as run_open:
            self.assertEqual(open_destination(d,0),('openai',True,'app'))
            self.assertEqual(run_open.call_args.args[0],['/usr/bin/open','-b','com.openai.codex'])
        with patch('conversation_open.subprocess.run',side_effect=OSError('not available')):
            self.assertEqual(open_destination(None,1),('claude',False,'app'))

    def test_real_usb_bridge_targets_both_providers_without_clearing_unrelated_alerts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); snapshot=root/'snapshot.json'; events=root/'events.json'; log=root/'opens.jsonl'
            save_snapshot({'version':1,'generated_at':time.time(),'available':{'codex':True,'chatgpt':False},
                           'unread':[{'kind':'codex','id':'reply','readRevision':'r'},
                                     {'kind':'codex','id':'question','readRevision':'q','attention':'question'}]},snapshot)
            save_snapshot({'version':1,'pending':{'cli-id':{'target':'claude','kind':'completion','at':1}}},events)
            class Desktop:
                aliases={'cli-id':'desktop:'+LOCAL_B}
                def pending(self):
                    return {'desktop:'+LOCAL_A:{'target':'claude','kind':'question','at':2,'source':'desktop'},
                            'desktop:'+LOCAL_B:{'target':'claude','kind':'completion','at':1,'source':'desktop'}}
            before=events.read_bytes(); master,slave=pty.openpty(); stopped=threading.Event(); opened=queue.Queue()
            args=argparse.Namespace(port=os.ttyname(slave),snapshot=snapshot,max_age=180,local_feeds=True,
                claude_events=events,claude_desktop=Desktop(),claude_code_enabled=True,dry_run=False,event_log=log)
            def dispatch(destination,provider,**kwargs):
                opened.put((provider,destination))
                return (destination.target if destination else 'openai'),True,('conversation' if destination and destination.url else 'app')
            def receive_revision():
                data=bytearray(); deadline=time.monotonic()+4
                while time.monotonic()<deadline:
                    if select.select([master],[],[],.1)[0]:data.extend(os.read(master,4096))
                    for line in data.splitlines():
                        fields=line.split()
                        if fields[:2]==[b'@D3',b'STATE']:return fields[2]
                self.fail('No state frame')
            thread=threading.Thread(target=run,args=(args,stopped))
            try:
                with patch('dotty_companion.open_destination',side_effect=dispatch):
                    thread.start(); revision=receive_revision()
                    os.write(master,b'@D2 OPEN 0 0\n')
                    with self.assertRaises(queue.Empty):opened.get(timeout=.15)
                    for provider,expected in ((0,'codex://threads/question'),(1,'claude://code/continue?session='+LOCAL_A),
                                              (0,'codex://threads/reply'),(1,'claude://code/continue?session='+LOCAL_B)):
                        os.write(master,b'@D2 OPEN '+revision+b' '+str(provider).encode()+b'\n')
                        actual,destination=opened.get(timeout=3)
                        self.assertEqual((actual,destination.url),(provider,expected))
                        time.sleep(.8)  # Existing physical-tap debounce.
                    self.assertEqual(events.read_bytes(),before)
                    self.assertEqual(read_snapshot(snapshot,180)[:3],(2,0,1))
                    self.assertEqual(len(log.read_text().splitlines()),4)
            finally:
                stopped.set();thread.join(timeout=4);os.close(master);os.close(slave)
                self.assertFalse(thread.is_alive())


if __name__=='__main__':unittest.main()
