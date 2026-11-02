const $ = id => document.getElementById(id);
const road = $('road'), length = road.getTotalLength();
const cards = new Map(), markers = new Map();
let state, selected, previousStations, reviewKey, animation, busy = false;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');

function make(tag, text, cls) {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  if (text !== undefined) element.textContent = text;
  return element;
}
function svg(tag, attributes, parent) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key,value] of Object.entries(attributes)) el.setAttribute(key,value);
  parent.append(el);
  return el;
}
async function api(path, method='GET') {
  const response = await fetch(path,{method,cache:'no-store'});
  if (!response.ok) throw Error((await response.json().catch(()=>({}))).detail || 'Request failed');
  return response.json();
}
function point(km) { return road.getPointAtLength(length * km / state.road_end); }
function showStatus(text) { $('status').textContent = text; }
async function command(path, message) {
  if (busy) return;
  busy = true;
  try { await api(path,'POST'); showStatus(message); }
  catch (err) { showStatus(err.message); }
  finally { busy=false; }
}

function drawStations() {
  const key = JSON.stringify(state.chargers);
  if (key === previousStations) return;
  previousStations = key;
  $('stations').replaceChildren(); $('depots').replaceChildren();
  state.chargers.forEach((s,i) => {
    const pos=point(s.road_km), above=i%2===0, sign=above?-1:1;
    const free=s.online && s.free_ports>0, status=!s.online?'Offline':free?s.free_ports+' free':'Occupied';
    const group=svg('g',{transform:'translate('+pos.x+' '+pos.y+')'},$('stations'));
    svg('line',{x1:0,y1:0,x2:0,y2:sign*28,stroke:'#728e82','stroke-width':2},group);
    svg('rect',{x:-6,y:sign*28-6,width:12,height:12,rx:3,fill:free?'#168160':'#96734a'},group);
    svg('text',{x:0,y:above?-41:51,'text-anchor':'middle',class:'maplabel'},group).textContent=s.id;
    const row=make('div',undefined,'station'), name=make('div');
    name.append(make('strong',s.id),make('span','Km '+s.road_km+' · '+s.connector,'sub'));
    row.append(name,make('span',status,'tag'+(free?'':' warn'))); $('depots').append(row);
  });
}
function updateMap() {
  const selectedVan=state.vehicles.find(v=>v.id===selected);
  const station=state.chargers.find(s=>s.id===selectedVan?.charger_id);
  let path='';
  if (station) {
    const from=selectedVan.road_km, span=station.road_km-from;
    for(let i=0;i<=40;i++){const p=point(from+span*i/40);path+=(i?'L':'M')+p.x+' '+p.y+' ';}
  }
  $('route').setAttribute('d',path);
  const now=performance.now();
  state.vehicles.forEach((v,i)=>{
    let m=markers.get(v.id);
    if(!m){
      const group=svg('g',{'aria-label':v.id},$('vans'));
      const ring=svg('circle',{r:13,fill:'none',stroke:'#1d426c','stroke-width':2},group);
      const dot=svg('circle',{r:10,fill:i%2?'#62529a':'#245584',stroke:'white','stroke-width':2},group);
      svg('text',{y:4,'text-anchor':'middle',class:'vanlabel'},group).textContent=v.id.split('-').pop();
      const title=svg('title',{},group);
      group.addEventListener('click',()=>selectVan(v.id));group.style.cursor='pointer';
      m={group,ring,dot,title,from:v.road_km,to:v.road_km,current:v.road_km,start:now};
      markers.set(v.id,m);
    }
    if(m.to!==v.road_km){m.from=m.current;m.to=v.road_km;m.start=now;}
    m.ring.style.opacity=v.id===selected?'1':'0';
    m.title.textContent=v.id+' · Km '+v.road_km;
  });
  cancelAnimationFrame(animation); animate(now);
}
function animate(now) {
  let moving=false;
  for(const m of markers.values()){
    const t=reducedMotion.matches?1:Math.min(1,Math.max(0,(now-m.start)/1050));
    m.current=m.from+(m.to-m.from)*t;
    const p=point(m.current);m.group.setAttribute('transform','translate('+p.x+' '+p.y+')');
    moving ||= t<1 && m.from!==m.to;
  }
  if(moving) animation=requestAnimationFrame(animate);
}
function selectVan(id){selected=id;render();}
function newCard(id) {
  const card=make('article',undefined,'vehicle'), head=make('div',undefined,'vhead');
  const name=make('button',id,'id'), badge=make('span'), info=make('p',undefined,'sub');
  name.onclick=()=>selectVan(id);head.append(name,badge);
  const meter=make('div',undefined,'meter'),fill=make('i');meter.append(fill);
  const note=make('p'), actions=make('div',undefined,'buttons');
  for(const [label,battery] of [['Low battery',8],['Critical battery',2],['Recovered',26]]){
    const btn=make('button',label);
    btn.onclick=()=>{selectVan(id);command('/demo/'+encodeURIComponent(id)+'/'+battery,'Drive paused · battery reading sent for '+id);};
    actions.append(btn);
  }
  const review=make('button','Investigate alert','primary');
  review.onclick=()=>investigate(id,review);actions.append(review);
  card.append(head,info,meter,note,actions);$('fleet').append(card);
  return {card,name,badge,info,fill,note,review};
}
async function investigate(id,button){
  selectVan(id);
  const v=state.vehicles.find(v=>v.id===id);
  const key=[id,v.alert_id,v.last_sequence].join('/');
  button.disabled=true;reviewKey=key;
  try{
    showStatus('Investigating '+id+'…');
    const result=await api('/alerts/'+v.alert_id+'/review','POST');
    if(reviewKey!==key)return;
    $('review-title').textContent=id+' · '+result.mode;
    $('explanation').textContent=result.summary;
    $('evidence').replaceChildren(...result.facts.options.map(s=>make('li',s.charger+' · '+s.distance_km+' km · '+s.reason)));
    $('tools').textContent='Safe range '+result.facts.safe_km+' km'+(result.trace.length?' · Tools: '+result.trace.join(' → '):'');
    $('review').hidden=false;$('review').scrollIntoView({block:'nearest'});showStatus('Review ready.');
  }catch(err){showStatus(err.message);}finally{button.disabled=false;}
}
function render(){
  if(!selected)selected=state.vehicles[0]?.id;
  $('count').textContent=state.vehicles.length;
  $('available').textContent=state.chargers.filter(s=>s.online&&s.free_ports>0).length+' / '+state.chargers.length;
  $('alerts').textContent=state.vehicles.filter(v=>v.alert_id).length;
  $('drive').textContent=state.running?'Ⅱ Pause drive':'▶ Start drive';
  $('drive').disabled=false;$('reset').disabled=false;$('drive-note').textContent=state.drive_note;
  const focus=state.vehicles.find(v=>v.id===selected);
  if(reviewKey && reviewKey!==[selected,focus?.alert_id,focus?.last_sequence].join('/')){
    $('review').hidden=true;reviewKey=null;
  }
  drawStations();updateMap();
  for(const v of state.vehicles){
    if(!cards.has(v.id))cards.set(v.id,newCard(v.id));
    const c=cards.get(v.id),pct=Math.round(100*v.battery_kwh/v.capacity_kwh);
    c.card.className='vehicle'+(v.id===selected?' selected':'')+(v.alert_id?' low':'');
    c.name.setAttribute('aria-pressed',String(v.id===selected));
    c.badge.className='tag'+(v.alert_id?' warn':'');
    c.badge.textContent=v.alert_id?(v.charger_id?'Low battery':'Assistance'):'Healthy';
    c.info.textContent=pct+'% · '+v.battery_kwh+' / '+v.capacity_kwh+' kWh · Km '+v.road_km;
    c.fill.style.width=pct+'%';c.note.textContent=v.reason||'Battery above alert threshold.';
    c.review.hidden=!v.alert_id;
  }
}
$('drive').onclick=()=>command('/drive/'+(state.running?'pause':'start'),state.running?'Pausing drive…':'Starting drive…');
$('reset').onclick=()=>command('/drive/reset','Vans returning to their initial demo readings.');
async function poll(){
  try{state=await api('/overview');render();$('connection').textContent='● Connected';}
  catch(err){$('connection').textContent='Reconnecting…';$('drive').disabled=true;$('reset').disabled=true;showStatus(err.message);}
  setTimeout(poll,900);
}
poll();
