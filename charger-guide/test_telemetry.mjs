import test from 'node:test';
import assert from 'node:assert/strict';
import {TelemetryOutbox, retryDelay} from './cab_watch/assets/telemetry.mjs';

test('pending detour survives refresh and is acknowledged before the return reading',()=>{
  const storage={data:new Map(),getItem(key){return this.data.get(key);},setItem(key,value){this.data.set(key,value);}};
  const queue=new TelemetryOutbox(storage,'manager-a');
  queue.append([{trip_id:'x',sequence:11,lat:13,lon:80}]);
  queue.append([{trip_id:'x',sequence:12,lat:13.01,lon:80}]);
  assert.throws(()=>queue.append([{sequence:13}]));
  const restored=new TelemetryOutbox(storage,'manager-a');
  assert.equal(restored.first()[0].sequence,11);
  assert.equal(new TelemetryOutbox(storage,'manager-b').length,0);
  restored.acknowledge();
  assert.equal(new TelemetryOutbox(storage,'manager-a').first()[0].sequence,12);
  restored.acknowledge();
  assert.equal(new TelemetryOutbox(storage,'manager-a').length,0);
});

test('retry respects server delay and stops on a missing sequence',()=>{
  assert.equal(retryDelay({status:429,retryAfter:5000},0),5000);
  assert.equal(retryDelay({},9),30000);
  assert.equal(retryDelay({status:409},0),null);
});

test('large fleets upload bounded chunks without losing readings',()=>{
  const storage={value:null,getItem(){return this.value;},setItem(key,value){this.value=value;}};
  const queue=new TelemetryOutbox(storage,'manager');
  queue.append(Array.from({length:60},(_,id)=>({trip_id:String(id),sequence:1})));
  const received=[];
  while(queue.length){
    assert.ok(queue.first().length<=25);
    received.push(...queue.first());
    queue.acknowledge();
  }
  assert.equal(received.length,60);
  assert.equal(new Set(received.map(r=>r.trip_id)).size,60);
});

test('upgrade replays pending passenger GPS and skips obsolete depot movements',()=>{
  const storage={value:null,getItem(){return this.value;},setItem(key,value){this.value=value;}};
  const queue=new TelemetryOutbox(storage,'manager');
  queue.append([{trip_id:'pickup',sequence:7,leg:0},{trip_id:'direct',sequence:1,leg:0},
    {trip_id:'ride',sequence:9,leg:1},{trip_id:'return',sequence:4,leg:2}]);
  queue.upgrade([{id:'pickup',last_sequence:6,lat:null,motion:{mode:'route',index:0}},
    {id:'direct',last_sequence:0,lat:null,motion:null},{id:'ride',last_sequence:8,lat:13},
    {id:'return',completed:true}]);
  assert.deepEqual(queue.first(),[{trip_id:'direct',sequence:1},{trip_id:'ride',sequence:9}]);
  assert.equal(queue.hasLegacyReadings(),false);
});
