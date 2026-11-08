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
function render(){
 layout();
 if(pairing){$('board').innerHTML='<article class="card pairing"><div class="cardtop"><span class="status">Display setup</span></div><p>Enter this code in the administration panel to assign this screen to a room.</p><div class="paircode" dir="ltr">'+esc(pairing.code.slice(0,3)+' '+pairing.code.slice(3))+'</div><p>The code renews automatically.</p></article>';$('queue').innerHTML='';$('upcoming').hidden=true;clock();return}
 const t=Date.now()+offset,flights=state?.flights||[];
 $('board').innerHTML=(screen==='hall'?rooms:[screen]).map(room=>{
  const f=flights.find(f=>f.room===room&&!f.done&&f.start&&new Date(f.end)>t);
  const next=flights.filter(x=>!x.done&&!x.start&&x.briefing).sort((x,y)=>x.briefing<y.briefing?-1:1)[0];
  let content='<div class="idle">'+(state?'Waiting for the next flight':'Waiting for flight schedule')+'</div>'+(next?'<p class="nextup">Next briefing <b dir="ltr">'+fmt(next.briefing)+'</b> · <span dir="ltr">'+esc(next.number.replace(/^KU/,'KU '))+'</span> '+esc(next.destination)+'</p>':'<p>Wishing you a pleasant flight</p>');
  if(f)content='<div class="flight" dir="ltr">'+esc(f.number.replace(/^KU/,'KU '))+'</div><div class="destination">'+esc(f.destination)+'</div>'+crewHtml(f)+'<div class="times"><span>'+fmt(f.start)+' – '+fmt(f.end)+'</span><span class="countdown" data-end="'+esc(f.end)+'"></span></div>';
  return '<article class="card '+(f?'active':state?'free':'waiting')+'"><div class="cardtop"><span class="room" dir="ltr">'+room+'</span><span class="status">'+(f?'Briefing in progress':state?'Available':'Waiting for updates')+'</span></div>'+content+'</article>';
 }).join('');
 const queue=flights.filter(f=>!f.done&&!f.start),page=Math.floor(t/15000)%Math.max(1,Math.ceil(queue.length/4)),upcoming=queue.slice(page*4,page*4+4);
 $('queue').innerHTML=upcoming.length?'<div class="qrow"><span>Flight</span><span>Destination</span><span>Briefing</span><span>Status</span></div>'+upcoming.map(f=>'<div class="qrow"><span dir="ltr">'+esc(f.number.replace(/^KU/,'KU '))+'</span><span>'+esc(f.destination)+'</span><span dir="ltr">'+df(f.briefing)+' '+fmt(f.briefing)+'</span><span>'+(new Date(f.briefing)<=t?'Waiting for a room':'Upcoming')+'</span></div>').join(''):'<p>'+(state?'No flights in the queue':'Waiting for flight schedule updates')+'</p>';
 clock();
}
function feedNotice(){
 const feed=state?.feed||{},last=feed.last_success;
 $('updated').textContent=last?'Flight schedule updated '+df(last)+' '+fmt(last):'Flight schedule not yet received';
 const stale=!last||Date.now()+offset-new Date(last)>8*3600e3;
 $('notice').textContent=stale?(last?'Flight schedule last updated '+df(last)+' '+fmt(last)+' — please confirm times with the flight dispatcher.':'Waiting for the first flight schedule update.'):'';
}
function clock(){
 $('clock').textContent=fmt(Date.now()+offset);
 document.querySelectorAll('[data-end]').forEach(el=>{const n=Math.max(0,Math.ceil((new Date(el.dataset.end)-Date.now()-offset)/1000));el.textContent='Remaining '+Math.floor(n/60)+':'+String(n%60).padStart(2,'0')});
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
 const t=Date.now();
 state={flights:[...rooms.map((r,i)=>({id:'demo-'+i,room:r,number:'DEMO '+(101+i),destination:['Cairo','Dubai','London','Bangkok','Riyadh','Doha','Jeddah'][i],sccm:'Demo crew '+(i+1),start:new Date(t-60000).toISOString(),end:new Date(t+540000).toISOString(),done:0})),{number:'DEMO 108',destination:'Demo flight',briefing:new Date(t+600000).toISOString(),done:0}]};
 $('notice').textContent='Demo preview — not operational data';$('updated').textContent='Demo data';
}
render();refresh();setInterval(clock,1000);setInterval(()=>{if(demo)render();else refresh()},5000);
