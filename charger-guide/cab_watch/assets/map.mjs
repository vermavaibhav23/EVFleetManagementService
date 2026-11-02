export function svg(parent,tag,attrs,text){
  const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))el.setAttribute(key,value);
  if(text!==undefined)el.textContent=text;
  parent.append(el);
  return el;
}
export function cityMap(xs,ys){
  const layer=document.querySelector('#geography');layer.replaceChildren();
  const rect=(x,y,width,height,fill,extra={})=>svg(layer,'rect',{x,y,width,height,fill,...extra});
  const path=(d,attrs)=>svg(layer,'path',{d,fill:'none',...attrs});
  const label=(x,y,text,cls)=>svg(layer,'text',{x,y,class:cls},text);
  rect(0,0,960,560,'#e8e6dc');
  const xlines=[-23,...xs,936], ylines=[-12,...ys,573];
  const parks=new Set(['2,1','4,3','6,0','1,4','5,5']);
  for(let i=0;i<xlines.length-1;i++)for(let j=0;j<ylines.length-1;j++){
    const x=xlines[i]+7,y=ylines[j]+7,w=xlines[i+1]-x-7,h=ylines[j+1]-y-7;
    if(parks.has(i+','+j)){
      rect(x,y,w,h,'#c6dbb5',{rx:6});
      path('M'+(x+4)+' '+(y+h-8)+'q'+(w/2)+' '+(-h*.85)+' '+(w-8)+' '+(-h+15),{stroke:'#f4edcf','stroke-width':4});
      for(let k=0;k<7;k++)svg(layer,'circle',{cx:x+7+(k*17)%(w-14),cy:y+8+(k*23)%(h-16),r:3+k%3,fill:'#9dbc94'});
    }else{
      rect(x,y,w,h,['#e5e2d7','#e3dfd6','#eee6d8'][(i+j)%3],{rx:4});
      for(let a=0;a<3;a++)for(let b=0;b<2;b++){
        const bx=x+4+a*w/3,by=y+4+b*h/2+(a%2)*2;
        rect(bx,by,w/3-7,h/2-9,['#d5d1c5','#d4cfc2','#dbd5c9','#d5d6ce'][(a+b+i+j)%4],
             {rx:2,stroke:'#c6c5b9','stroke-width':.8});
        if((a+b+i)%3===0)rect(bx+3,by+3,w/6,3,'#e9e5d9');
      }
    }
  }
  path('M905 -10 Q874 72 895 152T892 315 Q871 396 903 470T900 575H980V-10Z',
       {fill:'#c0e0e5',stroke:'#a5cccb','stroke-width':4});
  path('M914 -10 Q883 72 904 152T901 315 Q880 396 912 470T909 575',
       {stroke:'#d7edf0','stroke-width':2});
  for(const x of xs){const d='M'+x+' 0V560';path(d,{stroke:'#c6c6b8','stroke-width':11});path(d,{stroke:'#fcfaf0','stroke-width':8});}
  for(const y of ys){const d='M0 '+y+'H882';path(d,{stroke:'#c6c6b8','stroke-width':13});path(d,{stroke:'#fff9e8','stroke-width':10});}
  const names=['College Avenue','Market Street','Museum Road','Civic Boulevard','Garden Street','Ferry Road'];
  ys.forEach((y,i)=>label(18,y-9,names[i],'roadname'));
  label(186,114,'COLLEGE GREEN','place');label(427,284,'CIVIC PARK','place');
  label(634,40,'NORTH END','place');label(83,366,'GARDENS','place');
  label(503,441,'HARBOUR FIELD','place');label(638,520,'PORT DISTRICT','place');
  const water=label(929,395,'Harbour Channel','water');water.setAttribute('transform','rotate(-90 929 395)');
  rect(15,507,149,38,'#fffefa',{rx:5,opacity:.92});
  path('M27 524v5h83v-5',{stroke:'#46606a','stroke-width':2});label(27,521,'500 M','stamp');
  svg(layer,'text',{x:133,y:532,class:'stamp'},'↑ N');
}
export function makeLayers(cabs,choose){
  const routes=document.querySelector('#routes'), trails=document.querySelector('#trails'),pins=document.querySelector('#pins');
  routes.replaceChildren();trails.replaceChildren();pins.replaceChildren();
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
export function selectMap(cabs,id){
  cabs.forEach(c=>{
    c.map.g.setAttribute('opacity',c.id===id?'1':'.76');
    c.map.halo.setAttribute('visibility',c.id===id?'visible':'hidden');
    c.map.pin.setAttribute('aria-pressed',String(c.id===id));
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

