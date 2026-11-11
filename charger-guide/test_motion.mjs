import assert from 'node:assert/strict';
import test from 'node:test';
import {makeCab, motionSnapshot, divert, advance, rejoin} from './cab_watch/assets/motion.mjs';

// A---B---C---D is the plan. A---E---F---C is a longer side road.
const nodes = {P:[-80,0], A:[0,0], B:[100,0], C:[200,0], D:[300,0], E:[0,100], F:[200,100]};
const network = {nodes, roads:['PA','AB','BC','CD','AE','EF','FC']};
const stops = ['P','A','B','C','D'];
function trip() {
  return makeCab({route:stops}, network);
}
function until(cab, condition) {
  for (let i=0;i<100 && !condition();i++) advance(cab);
  assert.ok(condition());
}

test('a queued turn allows a U-turn and excludes the planned exit', t=>{
  const random=t.mock.method(Math,'random',()=>0);
  for(const [fraction,target] of [[0,'P'],[0.99,'E']]){
    random.mock.mockImplementation(()=>fraction);
    const cab=trip();
    advance(cab);
    divert(cab);
    assert.equal(cab.mode,'turn requested');
    until(cab,()=>cab.mode==='detour');
    assert.deepEqual(cab.point,nodes.A);
    assert.deepEqual(motionSnapshot(cab),{mode:'detour',trail:['A'],target});
  }
});

test('retrace reverses mid-road and preserves loops across reload', t=>{
  // A -> E -> A (back on the planned route) -> E, then request return mid-road.
  const random=t.mock.method(Math,'random',()=>0.99);
  const cab=trip();
  divert(cab);
  until(cab,()=>cab.mode==='detour');
  random.mock.mockImplementation(()=>0);
  until(cab,()=>cab.trail.length===2);
  random.mock.mockImplementation(()=>0.99);
  until(cab,()=>cab.trail.length===3);
  assert.equal(cab.mode,'detour');
  assert.deepEqual(cab.trail,['A','E','A']);
  advance(cab);
  const restored=makeCab({route:stops,motion:motionSnapshot(cab)},network);
  restored.point=[...cab.point];
  rejoin(restored);
  assert.deepEqual(restored.point,cab.point);
  assert.deepEqual(restored.recovery,['A','E','A']);
  advance(restored);
  assert.deepEqual(restored.point,nodes.A);
  assert.equal(restored.mode,'returning');
  const again=makeCab({route:stops,motion:motionSnapshot(restored)},network);
  again.point=[...restored.point];
  until(again,()=>again.recovery.length===1);
  assert.deepEqual(again.point,nodes.E);
  until(again,()=>again.mode==='route');
  assert.deepEqual(again.point,nodes.A);
  assert.equal(again.index,1);
  until(again,()=>again.mode==='arrived');
});

test('an old checkpoint with only its current road can still return',()=>{
  const cab=makeCab({route:stops,motion:{mode:'detour',source:'E',target:'F'}},network);
  cab.point=[50,100];
  rejoin(cab);
  assert.deepEqual(cab.recovery,['E','A']);
  until(cab,()=>cab.mode==='route');
  assert.deepEqual(cab.point,nodes.A);
});

test('return before the queued turn keeps the original route',()=>{
  const cab=trip();
  divert(cab);
  rejoin(cab);
  assert.equal(cab.mode,'route');
  until(cab,()=>cab.mode==='arrived');
  assert.deepEqual(cab.point,nodes.D);
});

test('direct ride starts at pickup and stops on the final junction',()=>{
  const cab=makeCab({route:['A','B','C']},network);
  assert.deepEqual(cab.point,nodes.A);
  until(cab,()=>cab.mode==='arrived');
  assert.deepEqual(cab.point,nodes.C);
  assert.equal(advance(cab),false);
});

test('each mode saves just what the next movement needs after reload',()=>{
  const route = ['P','A','B','C','D'];
  const cases = [
    [{mode:'route',index:1},[30,0]],
    [{mode:'turn requested',index:1},[30,0]],
    [{mode:'detour',trail:['A'],target:'E'},[0,30]],
    [{mode:'returning',recovery:['F','C']},[80,100]],
    [{mode:'arrived'},nodes.D]
  ];
  for(const [motion,point] of cases){
    const old={index:1,source:'A',target:'E',recovery:['F','C'],...motion};
    const original=makeCab({route,motion:old},network);
    const saved=motionSnapshot(original);
    assert.deepEqual(saved,motion);
    const resumed=makeCab({route,motion:saved},network);
    for(const cab of [original,resumed]) cab.point=[...point];
    assert.equal(advance(original),advance(resumed));
    assert.deepEqual(original.point,resumed.point);
    assert.deepEqual(motionSnapshot(original),motionSnapshot(resumed));
  }
});
