export function svg(parent,tag,attrs,text){
  const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);
  if(text!==undefined)el.textContent=text;
  parent.append(el);
  return el;
}
export function cityMap(network,pick){
  const layer=document.querySelector('#geography'), labels=document.querySelector('#intersections');
  layer.replaceChildren();labels.replaceChildren();
  svg(layer,'rect',{width:960,height:560,fill:'#f5f4ee'});
  svg(layer,'path',{d:'M910 0 Q870 120 925 230 T930 390 Q898 465 938 560 H960 V0 Z',fill:'#cbe1e4'});
  for(const [x,y,title] of [[74,38,'NORTH QUAY'],[350,333,'MIDTOWN'],[699,555,'HARBOUR DISTRICT']]){
    svg(layer,'text',{x,y,class:'district'},title);
  }
  for(const [a,b] of network.roads){
    const [x1,y1]=network.nodes[a],[x2,y2]=network.nodes[b];
    svg(layer,'line',{x1,y1,x2,y2,stroke:'#a9afb0','stroke-width':1.6});
  }
  for(const [name,[x,y]] of Object.entries(network.nodes)){
    const g=svg(labels,'g',{class:'junction',role:'button',tabindex:0,'aria-label':'Intersection '+name});
    svg(g,'circle',{cx:x,cy:y,r:13,fill:'transparent'});
    svg(g,'circle',{cx:x,cy:y,r:4,fill:'#fff',stroke:'#697a80','stroke-width':1.5});
    svg(g,'text',{x:x+8,y:y-10,class:'node-label'},name);
    g.onclick=()=>pick(name);
    g.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();pick(name);}};
  }
}
export function makeLayers(cabs,choose){
  const routes=document.querySelector('#routes'), trails=document.querySelector('#trails'),pins=document.querySelector('#pins');
  cabs.forEach(c=>{
    const g=svg(routes,'g',{}), points=c.path.map(p=>p.join(',')).join(' ');
    svg(g,'polyline',{points,fill:'none',stroke:'white','stroke-width':8,'stroke-linejoin':'round'});
    svg(g,'polyline',{points,fill:'none',stroke:c.color,'stroke-width':4,'stroke-linejoin':'round'});
    const [ox,oy]=c.nodes[0],[dx,dy]=c.nodes.at(-1);
    svg(g,'circle',{cx:ox,cy:oy,r:7,fill:'white',stroke:c.color,'stroke-width':3});
    svg(g,'rect',{x:dx-6,y:dy-6,width:12,height:12,fill:'white',stroke:c.color,'stroke-width':3,transform:'rotate(45 '+dx+' '+dy+')'});
    const trail=svg(trails,'polyline',{points:'',fill:'none',stroke:c.color,'stroke-width':3,'stroke-dasharray':'4 5','stroke-linecap':'round'});
    const pin=svg(pins,'g',{class:'cabpin',role:'button',tabindex:0,'aria-label':'Select Cab '+c.cab});
    pin.onclick=()=>choose(c.id);pin.onkeydown=e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();choose(c.id);}};
    const halo=svg(pin,'circle',{r:17,fill:'white',stroke:c.color,'stroke-width':2});
    svg(pin,'rect',{x:-12,y:-10,width:24,height:20,rx:7,fill:c.color,stroke:'white','stroke-width':1.5});
    svg(pin,'text',{x:0,y:4,'text-anchor':'middle',class:'pinlabel'},c.cab);
    svg(pin,'title',{},'Cab '+c.cab+' · '+c.origin+' to '+c.destination);
    pin.setAttribute('transform','translate('+ox+' '+oy+')');
    c.map={g,trail,pin,halo,display:[ox,oy],target:[ox,oy],from:[ox,oy],at:0};
  });
}
// Give every cab a visible stripe on roads used by more than one trip.
export function drawSharedRoads(cabs,network){
  const layer=document.querySelector('#shared');
  layer.replaceChildren();
  const owners=new Map();
  for(const cab of cabs){
    for(let i=1;i<cab.stops.length;i++){
      const edge=[cab.stops[i-1],cab.stops[i]].sort().join('');
      if(!owners.has(edge))owners.set(edge,[]);
      owners.get(edge).push(cab);
    }
  }
  for(const [edge,group] of owners){
    if(group.length<2)continue;
    const [start,end]=edge;
    const [x1,y1]=network.nodes[start],[x2,y2]=network.nodes[end];
    const length=Math.hypot(x2-x1,y2-y1);
    const stripe=Math.min(9,length/group.length);
    group.forEach((cab,i)=>svg(layer,'line',{x1,y1,x2,y2,stroke:cab.color,'stroke-width':3,
      'stroke-dasharray':[stripe,stripe*(group.length-1)].join(' '),'stroke-dashoffset':-i*stripe}));
  }
}
export function selectMap(cabs,id){
  cabs.forEach(c=>{
    c.map.g.setAttribute('opacity',c.id===id?'1':'.76');
    c.map.halo.setAttribute('visibility',c.id===id?'visible':'hidden');
    c.map.pin.setAttribute('aria-pressed',String(c.id===id));
    if(c.id===id){
      c.map.g.parentNode.append(c.map.g);
      c.map.pin.parentNode.append(c.map.pin);
    }
  });
}
export function showPoint(c,point){
  const m=c.map;
  if(Math.hypot(point[0]-m.target[0],point[1]-m.target[1])<.01)return;
  m.from=[...m.display];m.target=point;m.at=performance.now();
  c.trail.push(point);m.trail.setAttribute('points',c.trail.map(p=>p.join(',')).join(' '));
}
export function animate(cabs,now){
  let active=false;
  for(const c of cabs){
    const m=c.map,t=matchMedia('(prefers-reduced-motion: reduce)').matches?1:Math.min(1,(now-m.at)/850);
    m.display=m.target.map((v,i)=>m.from[i]+(v-m.from[i])*t);
    m.pin.setAttribute('transform','translate('+m.display.join(' ')+')');
    active ||= t<1;
  }
  return active;
}

