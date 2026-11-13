'use strict';
// Crew display: no controls. Room and orientation are assigned from the administration panel.
const $=id=>document.getElementById(id),rooms=['A-1','B-2','C-3','D-4','E-5','F-6','H-8'];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',hour:'2-digit',minute:'2-digit'}).format(new Date(s));
const df=s=>new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Kuwait',day:'2-digit',month:'short'}).format(new Date(s));
const params=new URLSearchParams(location.search),demo=params.get('demo')==='1';
const API=(window.CREW_API||'').replace(/\/$/,''),TOKEN_KEY='crew-display-token';
const store={get:k=>{try{return localStorage.getItem(k)}catch{return null}},set:(k,v)=>{try{localStorage.setItem(k,v)}catch{}},del:k=>{try{localStorage.removeItem(k)}catch{}}};
let screen=rooms.includes(params.get('screen'))?params.get('screen'):'hall',orientation=params.get('orientation')||'auto';
let state=null,offset=0,lastFetch=0,busy=false,pairing=null;
const person='<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="7" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3M9 14l3 6 3-6"/></svg>';
const crewHtml=f=>{const l=f.crew&&f.crew.length?f.crew:[];return l.length?'<div class="crewlist">'+l.map(p=>'<div class="sccm role-'+esc(p.role).toLowerCase()+'">'+person+'<b>'+esc(p.role)+'</b><span>'+esc(p.name)+'</span></div>').join('')+'</div>':'<div class="sccm">'+person+'<b>SCCM</b><span>SCCM not assigned</span></div>'};
function layout(){
 document.body.dataset.orientation=['auto','portrait','landscape'].includes(orientation)?orientation:'auto';
 document.body.classList.toggle('roomview',screen!=='hall');$('upcoming').hidden=screen!=='hall';
 $('title').textContent=screen==='hall'?'Your journey starts here':'Briefing room '+screen;
}
// ---- Flow board: cards sized by priority, pushed in and out with animation ----
const cards=new Map();let lastLayoutKey='';
function weightOf(f,t){const b=new Date(f.briefing);if(b<=t)return 3;return (b-t)<=600000?2:1}
function rects(items,W,H,portrait){
 const n=items.length,gap=Math.round(Math.min(W,H)*0.02)+8;if(!n)return [];
 const rowsN=portrait?(n<=2?n:Math.ceil(n/2)):(n<=3?1:n<=8?2:3),rows=[];let i=0;
 for(let r=0;r<rowsN;r++){const c=Math.ceil((n-i)/(rowsN-r));rows.push(items.slice(i,i+c));i+=c}
 const rw=rows.map(row=>Math.max(...row.map(x=>x.w))),sumH=rw.reduce((a,b)=>a+b,0),totalH=H-gap*(rows.length-1),out=[];let y=0;
 rows.forEach((row,ri)=>{const h=totalH*rw[ri]/sumH,sw=row.reduce((a,x)=>a+x.w,0),totalW=W-gap*(row.length-1);let x=0;
  row.forEach((it,k)=>{const w=totalW*it.w/sw;out.push({id:it.id,x,y,w,h,first:k===0,last:k===row.length-1&&ri===rows.length-1});x+=w+gap});y+=h+gap});
 return out}
function cardHtml(f,room,t){
 if(!f)return '<div class="cardtop"><span class="room" dir="ltr">'+esc(room||'')+'</span><span class="status">'+(state?'Available':'Waiting for updates')+'</span></div><div class="idlebox"><div class="idle">'+(state?'Waiting for the next flight':'Waiting for flight schedule')+'</div>'+nextHtml()+'</div>';
 const before=new Date(f.briefing)>t;
 return '<div class="cardtop"><span class="room" dir="ltr">'+esc(f.room)+'</span><span class="status">'+(before?'Reporting at '+fmt(f.briefing):'Briefing in progress')+'</span></div>'+
  '<div class="flight" dir="ltr">'+esc((f.label||f.number).replace(/^KU/,'KU '))+'</div><div class="destination">'+esc(f.destination||'')+'</div>'+crewHtml(f)+
  '<div class="times"><span>Reporting '+fmt(f.briefing)+' · until '+fmt(f.end)+'</span><span class="countdown" data-brief="'+esc(f.briefing)+'" data-end="'+esc(f.end)+'"></span></div>'+
  '<div class="progress"><i data-pstart="'+esc(f.start)+'" data-pbrief="'+esc(f.briefing)+'" data-pend="'+esc(f.end)+'"></i></div>'+
  (window.landmarkSvg?'<div class="landmark">'+landmarkSvg(f.destination)+'</div>':'')}
function nextHtml(){const flights=state?.flights||[],next=flights.filter(x=>!x.done&&!x.start&&x.briefing).sort((x,y)=>new Date(x.briefing)-new Date(y.briefing))[0];
 return next?'<p class="nextup">Next briefing <b dir="ltr">'+fmt(next.briefing)+'</b> · <span dir="ltr">'+esc((next.label||next.number).replace(/^KU/,'KU '))+'</span> '+esc(next.destination||'')+'</p>':'<p>Wishing you a pleasant flight</p>'}
function flow(items,t){
 const board=$('board'),top=board.getBoundingClientRect().top,below=(screen==='hall'?$('upcoming').offsetHeight+24:0)+($('freeRooms')?.offsetHeight||0)+document.querySelector('footer').offsetHeight+64;
 const H=Math.max(260,innerHeight-top-below),W=board.clientWidth,portrait=document.body.dataset.orientation==='portrait'||(document.body.dataset.orientation!=='landscape'&&innerHeight>innerWidth);
 board.classList.add('flow');board.style.height=H+'px';
 const pos=rects(items,W,H,portrait),seen=new Set();
 pos.forEach((r,i)=>{const it=items[i];seen.add(it.id);let el=cards.get(it.id);
  if(!el){el=document.createElement('article');el.className='card flowcard entering'+(r.last?'':' grow');el.dataset.id=it.id;
   // the newest card slides in from the right; one placed between others grows in place
   Object.assign(el.style,{left:(r.last?W+60:r.x)+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});board.appendChild(el);cards.set(it.id,el);
   el.offsetWidth;requestAnimationFrame(()=>requestAnimationFrame(()=>el.classList.remove('entering','grow')))}
  if(el._html!==it.html){el.innerHTML=it.html;el._html=it.html}
  el.className='card flowcard '+it.cls+(it.lm?' has-lm':'')+(el.classList.contains('entering')?' entering':'')+(el.classList.contains('grow')?' grow':'');el._first=r.first;
  Object.assign(el.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});
  el.style.setProperty('--w',it.w);el.style.zIndex=2});
 for(const [id,el] of cards)if(!seen.has(id)){cards.delete(id);el.style.zIndex=1;
  // a finished card at the left edge is pushed out of the screen; one in the middle fades away in place
  if(el._first){el.classList.add('leaving');el.style.left=(-parseFloat(el.style.width)-80)+'px'}else el.classList.add('vanish');
  setTimeout(()=>el.remove(),1100)}
}
function freeRoomsBar(busy){
 let bar=$('freeRooms');if(screen!=='hall'){if(bar)bar.remove();return}
 if(!bar){bar=document.createElement('div');bar.id='freeRooms';$('board').after(bar)}
 const free=rooms.filter(r=>!busy.has(r));
 bar.innerHTML=free.length?'<span class="label">Available rooms</span>'+free.map(r=>'<span class="chip" dir="ltr">'+r+'</span>').join(''):'<span class="label">All rooms in use</span>';
}
function render(){
 layout();
 if(pairing){cards.clear();$('board').classList.remove('flow');$('board').style.height='';$('board').innerHTML='<article class="card pairing"><div class="cardtop"><span class="status">Display setup</span></div><p>Enter this code in the administration panel to assign this screen to a room.</p><div class="paircode" dir="ltr">'+esc(pairing.code.slice(0,3)+' '+pairing.code.slice(3))+'</div><p>The code renews automatically.</p></article>';$('queue').innerHTML='';$('upcoming').hidden=true;$('freeRooms')?.remove();clock();return}
 if(!$('board').classList.contains('flow'))$('board').innerHTML='';
 const t=Date.now()+offset,flights=state?.flights||[];
 const live=flights.filter(f=>!f.done&&f.start&&f.room&&new Date(f.end)>t&&(screen==='hall'||f.room===screen)).sort((a,b)=>new Date(a.briefing)-new Date(b.briefing));
 const items=live.map(f=>({id:f.id,w:screen==='hall'?weightOf(f,t):1,cls:'active'+(new Date(f.briefing)>t?' upnext':''),html:cardHtml(f,f.room,t),lm:true}));
 if(!items.length)items.push({id:'idle-'+screen,w:1,cls:state?'free idlecard':'waiting idlecard',html:cardHtml(null,screen==='hall'?'':screen,t)});
 freeRoomsBar(new Set(live.map(f=>f.room)));
 const queue=flights.filter(f=>!f.done&&!f.start&&f.briefing).sort((a,b)=>new Date(a.briefing)-new Date(b.briefing)),limit=document.body.dataset.orientation==='portrait'||innerHeight>innerWidth?6:3,upcoming=queue.slice(0,limit);
 $('queue').innerHTML=upcoming.length?'<div class="qrow"><span>Flight</span><span>Destination</span><span>Briefing</span><span>Status</span></div>'+upcoming.map(f=>'<div class="qrow"><span dir="ltr">'+esc((f.label||f.number).replace(/^KU/,'KU '))+'</span><span>'+esc(f.destination||'')+'</span><span dir="ltr">'+df(f.briefing)+' '+fmt(f.briefing)+'</span><span>'+(new Date(f.briefing)-1800000<=t?'Waiting for a room':'Upcoming')+'</span></div>').join(''):'<p>'+(state?'No flights in the queue':'Waiting for flight schedule updates')+'</p>';
 flow(items,t);clock();
}
addEventListener('resize',()=>render());
function feedNotice(){
 const feed=state?.feed||{},last=feed.last_success;
 $('updated').textContent=last?'Flight schedule updated '+df(last)+' '+fmt(last):'Flight schedule not yet received';
 const stale=!last||Date.now()+offset-new Date(last)>8*3600e3;
 $('notice').textContent=stale?(last?'Flight schedule last updated '+df(last)+' '+fmt(last)+' — please confirm times with the flight dispatcher.':'Waiting for the first flight schedule update.'):'';
}
function clock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const now=Date.now()+offset,b=el.dataset.brief?new Date(el.dataset.brief):0,before=b&&b>now,n=Math.max(0,Math.ceil(((before?b:new Date(el.dataset.end))-now)/1000)),txt=(before?'Starts in ':'Remaining ')+Math.floor(n/60)+':'+String(n%60).padStart(2,'0');el.textContent=txt;el.classList.toggle('before',!!before);const card=el.closest('.card');if(card){card.classList.toggle('upnext',!!before);const st=card.querySelector('.status');if(st&&card.classList.contains('active'))st.textContent=before?'Reporting at '+fmt(b):'Briefing in progress'}});
 document.querySelectorAll('[data-pend]').forEach(el=>{const now=Date.now()+offset,b=+new Date(el.dataset.pbrief),e=+new Date(el.dataset.pend),st=+new Date(el.dataset.pstart);const p=now<b?(now-st)/Math.max(1,b-st):(now-b)/Math.max(1,e-b);el.style.width=Math.max(0,Math.min(100,p*100))+'%';el.classList.toggle('pre',now<b)});
 if(!demo&&!pairing&&lastFetch&&Date.now()-lastFetch>20000){document.body.classList.add('stale');$('notice').textContent='Updates interrupted — please check with the flight dispatcher.'}
}
async function post(path,body){const r=await fetch(API+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(12000)});return [r.status,await r.json().catch(()=>({}))]}
async function startPairing(){
 try{const [status,d]=await post('/api/pair/start',{screen,orientation});if(status!==200)throw Error();pairing={code:d.code,secret:d.secret,until:Date.now()+(d.expires_in-30)*1000};$('notice').textContent='';$('updated').textContent='Not yet assigned to a room'}
 catch{pairing=null;$('notice').textContent='Unable to reach the briefing service. Retrying…'}
 render();
}
async function pollPairing(){
 if(Date.now()>pairing.until)return startPairing();
 try{const [status,d]=await post('/api/pair/poll',{secret:pairing.secret});if(status===404)return startPairing();if(d.paired&&d.token){store.set(TOKEN_KEY,d.token);pairing=null;await loadState()}}catch{}
}
async function loadState(){
 const token=store.get(TOKEN_KEY);
 if(!API){$('notice').textContent='Waiting for flight schedule updates';render();return}
 if(!token){if(!pairing)await startPairing();else await pollPairing();return}
 const res=await fetch(API+'/api/state',{headers:{Authorization:'Bearer '+token},signal:AbortSignal.timeout(12000)});
 if(res.status===401){store.del(TOKEN_KEY);state=null;return startPairing()}
 if(!res.ok)throw Error('offline');
 state=await res.json();offset=new Date(state.now)-Date.now();lastFetch=Date.now();document.body.classList.remove('stale');
 if(state.display){screen=state.display.screen||'hall';orientation=state.display.orientation||'auto'}
 feedNotice();render();
}
async function refresh(){
 if(demo||busy)return;busy=true;
 try{await loadState()}catch{document.body.classList.add('stale');$('notice').textContent='Updates interrupted — please check with the flight dispatcher.'}
 finally{busy=false}
}
if(demo){
 // Demo: fictional flights cycling through rooms so the movement can be previewed.
 const names=['Hyderabad','Cairo','Dubai','London','Istanbul','Bangkok','Riyadh','Paris','Kathmandu','Mumbai'];let seq=0,base=Date.now();
 const mk=(min,room)=>{const i=seq++,b=base+min*60000;return {id:'demo-'+i,room,number:'KU '+(101+i*2),destination:names[i%names.length],crew:[{role:i%3?'CCI':'FSS',name:'Demo Crew '+(i+1)}],briefing:new Date(b).toISOString(),start:new Date(b-1800000).toISOString(),end:new Date(b+600000).toISOString(),done:0}};
 state={flights:[mk(-4,'A-1'),mk(6,'B-2'),mk(22,'C-3')],feed:{}};
 setInterval(()=>{const fl=state.flights;if(fl.length>=5||(fl.length>1&&seq%2))fl.shift();else{const used=new Set(fl.map(f=>f.room));fl.push(mk(5+Math.round(Math.random()*20),rooms.find(r=>!used.has(r))))}
  base=Date.now();fl.forEach((f,k)=>{const b=Date.now()+(k===0?-3:k*7)*60000;f.briefing=new Date(b).toISOString();f.start=new Date(b-1800000).toISOString();f.end=new Date(b+600000).toISOString()})},6000);
 $('notice').textContent='Demo preview — not operational data';$('updated').textContent='Demo data';
}
render();refresh();setInterval(clock,1000);setInterval(()=>{if(demo)render();else refresh()},5000);
