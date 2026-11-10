import assert from 'node:assert/strict';
import test from 'node:test';
import {makeCab, divert, advance, rejoin} from './cab_watch/assets/motion.mjs';

// A---B---C---D is the plan. A---E---F---C is a longer side road.
const nodes = {P:[-80,0], A:[0,0], B:[100,0], C:[200,0], D:[300,0], E:[0,100], F:[200,100]};
const network = {nodes, roads:['PA','AB','BC','CD','AE','EF','FC']};
const stops = ['P','A','B','C','D'];
function trip() {
  return makeCab({stops, path:stops.map(n=>nodes[n])}, network);
}
function until(cab, condition) {
  for (let i=0;i<100 && !condition();i++) advance(cab);
  assert.ok(condition());
}

test('a wandering cab passes successive side-road junctions and rejoins at C',()=>{
  const cab=trip();
  advance(cab);
  divert(cab);
  assert.equal(cab.mode,'turn requested');
  const x=cab.point[0]; advance(cab);
  assert.ok(cab.point[0]>x);
  until(cab,()=>cab.mode==='detour');
  assert.deepEqual(cab.point,nodes.A);
  assert.equal(cab.to,'E');
  until(cab,()=>cab.from==='C');
  assert.equal(cab.mode,'detour');
  for(let i=0;i<200;i++)advance(cab);
  assert.equal(cab.mode,'detour');
  rejoin(cab);
  until(cab,()=>cab.mode==='arrived');
});

test('recovery minimizes the full remaining journey and rejoins sooner on a tie',()=>{
  for(const [steps,junction] of [[0,'A'],[2,'C'],[12,'C']]){
    const cab=trip();
    divert(cab);
    until(cab,()=>cab.from==='E');
    for(let i=0;i<steps;i++) advance(cab);
    const before=[...cab.point];
    rejoin(cab);
    assert.deepEqual(cab.point,before);
    assert.deepEqual(cab.recovery,junction==='A'?['E','A']:['F','C']);
    until(cab,()=>cab.mode==='route');
    assert.deepEqual(cab.point,nodes[junction]);
  }
});

// Real Harbour City coordinates: N-H is shorter than N-M, but the complete
// remaining passenger journey N-M-L is shorter than N-H-M-L.
const harbour = {nodes:{H:[338,195], M:[260,288], L:[96,305], N:[472,275]},
  roads:['HN','HM','MN','LM']};
function harbourDetour(point) {
  const cab=makeCab({planned_legs:{to_pickup:['H'],customer_ride:['H','M','L'],to_depot:['L']}},harbour);
  Object.assign(cab,{mode:'detour',from:'H',to:'N',point:[...point]});
  return cab;
}

test('at N, return chooses M instead of backtracking through H',()=>{
  const cab=harbourDetour(harbour.nodes.N);
  rejoin(cab);
  assert.deepEqual(cab.recovery,['N','M']);
  until(cab,()=>cab.mode==='route');
  assert.deepEqual(cab.point,harbour.nodes.M);
  until(cab,()=>cab.mode==='arrived');
  assert.deepEqual(cab.point,harbour.nodes.L);
});

test('near H, turning back to H is still cheaper for the remaining trip',()=>{
  const point=harbour.nodes.H.map((v,i)=>v+.1*(harbour.nodes.N[i]-v));
  const cab=harbourDetour(point);
  rejoin(cab);
  assert.deepEqual(cab.point,point);
  assert.deepEqual(cab.recovery,['H']);
});

test('between H and N, choose the cheaper full journey without teleporting',()=>{
  const point=harbour.nodes.H.map((v,i)=>v+.9*(harbour.nodes.N[i]-v));
  const cab=harbourDetour(point);
  rejoin(cab);
  assert.deepEqual(cab.point,point);
  assert.deepEqual(cab.recovery,['N','M']);
  advance(cab);
  assert.ok(cab.point[0]>point[0]);
});

test('a dispatched car visits pickup, drop-off, and returns to its depot',()=>{
  const legs=[
    {stops:['A','B'],path:[nodes.A,nodes.B]},
    {stops:['B','C'],path:[nodes.B,nodes.C]},
    {stops:['C','B','A'],path:[nodes.C,nodes.B,nodes.A]},
  ];
  const cab=makeCab({legs,stops:['A','B','C','B','A'],path:[nodes.A,nodes.B,nodes.C,nodes.B,nodes.A]},network);
  assert.equal(cab.leg,0);
  until(cab,()=>cab.leg===1);
  assert.deepEqual(cab.point,nodes.B);
  until(cab,()=>cab.leg===2);
  assert.deepEqual(cab.point,nodes.C);
  until(cab,()=>cab.mode==='arrived');
  assert.equal(cab.leg,2);
  assert.deepEqual(cab.point,nodes.A);
});

test('pickup at the depot skips the empty first leg',()=>{
  const legs=[
    {stops:['A'],path:[nodes.A]},
    {stops:['A','B'],path:[nodes.A,nodes.B]},
    {stops:['B','A'],path:[nodes.B,nodes.A]},
  ];
  const cab=makeCab({legs,stops:['A','B','A'],path:[nodes.A,nodes.B,nodes.A]},network);
  assert.equal(cab.leg,1);
  until(cab,()=>cab.mode==='arrived');
  assert.equal(cab.leg,2);
});

test('drop-off at the home depot completes the empty return leg',()=>{
  const legs=[
    {stops:['A','B'],path:[nodes.A,nodes.B]},
    {stops:['B','A'],path:[nodes.B,nodes.A]},
    {stops:['A'],path:[nodes.A]},
  ];
  const cab=makeCab({legs,stops:['A','B','A'],path:[nodes.A,nodes.B,nodes.A]},network);
  until(cab,()=>cab.mode==='arrived');
  assert.equal(cab.leg,2);
  assert.deepEqual(cab.point,nodes.A);
});


test('direct ride starts at pickup and stops on the final junction',()=>{
  const cab=makeCab({route:['A','B','C']},network);
  assert.deepEqual(cab.point,nodes.A);
  until(cab,()=>cab.mode==='arrived');
  assert.deepEqual(cab.point,nodes.C);
  assert.equal(advance(cab),false);
});
