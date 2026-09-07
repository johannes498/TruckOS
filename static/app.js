const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

function showView(id){
  document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x.dataset.view===id));
  document.querySelectorAll('.view').forEach(x=>x.classList.toggle('active',x.id===id));
  history.replaceState(null,'',`#${id}`);
}

document.querySelectorAll('.nav').forEach(b=>b.onclick=()=>showView(b.dataset.view));
document.querySelectorAll('[data-go]').forEach(b=>b.onclick=()=>showView(b.dataset.go));
const initial = location.hash.replace('#',''); if(document.getElementById(initial)) showView(initial);

document.querySelectorAll('.toggle-edit').forEach(btn=>btn.onclick=()=>{
  const box=btn.closest('.item')?.querySelector('.edit-box');
  box?.classList.toggle('hidden');
  btn.textContent=box?.classList.contains('hidden')?'Rediger':'Luk';
});

document.querySelectorAll('[data-confirm]').forEach(btn=>btn.addEventListener('click',e=>{
  if(!confirm(btn.dataset.confirm)) e.preventDefault();
}));

const f=document.getElementById('diagForm');
if(f) f.onsubmit=async e=>{
  e.preventDefault();
  const btn=f.querySelector('button'), box=document.getElementById('diagResult'), txt=document.getElementById('diagText'), mode=document.getElementById('diagMode');
  btn.disabled=true; btn.textContent='Analyserer...'; box.classList.remove('hidden'); txt.textContent='TruckOS arbejder...'; mode.textContent='';
  try{
    const r=await fetch('/api/diagnose',{
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({truck_id:document.getElementById('diagTruck').value,fault_code:document.getElementById('faultCode').value,symptoms:document.getElementById('symptoms').value})
    });
    const d=await r.json();
    txt.textContent=d.answer||d.error||'Ukendt fejl';
    mode.textContent=d.ai?'OpenAI':'Lokal fallback';
  }catch(_){txt.textContent='Kunne ikke kontakte TruckOS-serveren.'}
  btn.disabled=false; btn.textContent='Kør diagnose';
};
