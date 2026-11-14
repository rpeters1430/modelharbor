(() => {
"use strict";
const $ = id => document.getElementById(id);
let models = [];
let agents = [];
let selectedMatches = [];
// "Remember on this device" keeps the token in localStorage so it survives closing the tab and app updates;
// otherwise it lives only for this tab. Storage can be blocked (private mode), so every access is guarded.
const store = (kind, fn) => { try { return fn(window[kind]); } catch (_) { return null; } };
let token = store("localStorage", s => s.getItem("modelharbor_token")) || store("sessionStorage", s => s.getItem("modelharbor_token")) || "";
function saveToken(value, remember) {
 for (const kind of ["localStorage", "sessionStorage"]) store(kind, s => s.removeItem("modelharbor_token"));
 if (value) store(remember ? "localStorage" : "sessionStorage", s => s.setItem("modelharbor_token", value));
}
let view = "overview";
$("civitai-settings").onclick=async()=>{
 $("civitai-settings-dialog").showModal();$("civitai-token-input").value="";
 try{const x=await apiRequest("/api/settings/civitai");$("civitai-token-status").textContent=x.configured?"Token configured":"No token configured";}catch(e){$("civitai-token-status").textContent=e.message;}
};
$("civitai-token-close").onclick=()=>$("civitai-settings-dialog").close();
$("civitai-token-save").onclick=async()=>{
 const value=$("civitai-token-input").value.trim();if(!value){$("civitai-token-status").textContent="Enter a token first";return;}
 try{await apiRequest("/api/settings/civitai","PUT",{token:value});$("civitai-token-input").value="";$("civitai-token-status").textContent="Token saved";}catch(e){$("civitai-token-status").textContent=e.message;}
};
$("civitai-token-remove").onclick=async()=>{try{await apiRequest("/api/settings/civitai","DELETE");$("civitai-token-status").textContent="Token removed";}catch(e){$("civitai-token-status").textContent=e.message;}};
$("civitai-token-test").onclick=async()=>{try{const x=await apiRequest("/api/settings/civitai/test","POST");$("civitai-token-status").textContent=x.note;}catch(e){$("civitai-token-status").textContent=e.message;}};

$("workflow-check").onclick=async()=>{
 const file=$("workflow-file").files[0],target=$("workflow-results");
 if(!file){target.textContent="Choose a workflow JSON first";return;}
 if(file.size>2_000_000){target.textContent="Workflow exceeds 2 MB";return;}
 const button=$("workflow-check");button.disabled=true;target.replaceChildren(text("p","Checking workflow…","muted"));
 try{
  const workflow=JSON.parse(await file.text());
  const result=await apiRequest("/api/workflows/audit","POST",{workflow});
  target.replaceChildren(text("p",result.total+" referenced model files · "+result.missing+" missing","model-sub"));
  for(const dep of result.dependencies){
   const row=text("div","","workflow-dependency");
   row.append(text("strong",(dep.status==="installed"?"✓ ":dep.status==="possible_match"?"? ":"✕ ")+dep.name));
   row.append(text("span"," · "+dep.status.replace("_"," ")+" · "+dep.nodes.join(", "),"model-sub"));
   if(dep.status==="missing"){
    const find=text("button","Find in Browse","repo-action");find.type="button";
    find.onclick=()=>{$("browse-q").value=dep.name.replace(/\.[^.]+$/,"").replace(/[_-]+/g," ");$("browse-go").click();};
    row.append(find);
   }
   target.append(row);
  }
  target.append(text("p",result.note,"muted"));
 }catch(e){target.replaceChildren(text("p","Could not analyze workflow: "+e.message,"muted"));}
 finally{button.disabled=false;}
};
const fmt = n => { if (!Number.isFinite(n)) return "—"; if (n === 0) return "0 B"; const u = ["B","KB","MB","GB","TB","PB"]; const i = Math.min(Math.floor(Math.log(n)/Math.log(1000)),u.length-1); return (n/Math.pow(1000,i)).toFixed(i ? 1 : 0)+" "+u[i]; };
const text = (tag,value,cls) => { const e=document.createElement(tag); e.textContent=value; if(cls)e.className=cls; return e; };
function showNotice(message){ $("notice").textContent=message; $("notice").hidden=!message; }
function setStatus(online){ $("status").textContent=online?"● Connected":"● Disconnected"; $("status").classList.toggle("online",online); }
function openAuth(){ $("token").value=token; $("auth-dialog").showModal(); }
const VIEWS=["overview","library","browse","convert","devices"];
// The tab lives in the URL hash so Back returns to the previous tab instead of closing the installed app.
function switchView(next){view=next;if(location.hash.slice(1)!==next)location.hash=next;document.querySelector(".stats").hidden=next!=="overview";document.querySelectorAll(".tab").forEach(b=>b.classList.toggle("active",b.dataset.view===next));document.querySelectorAll(".view").forEach(e=>e.hidden=e.id!==next+"-view");$("heading").textContent=({overview:"Model overview",library:"Model library",browse:"Browse models",convert:"Convert models",devices:"Connected sources"})[next];}
let currentModel=null;
function reopen(){const fresh=models.find(x=>x.device===currentModel.device&&x.path===currentModel.path);if(fresh)details(fresh);}
async function showCivitaiVersions(item,target){
 const m=currentModel;target.replaceChildren(text("p","Loading versions…","muted"));
 try{
  const data=await apiRequest("/api/civitai/models/"+encodeURIComponent(item.id)+"?device="+encodeURIComponent(m.device)+"&path="+encodeURIComponent(m.path));
  target.replaceChildren(text("p","Pick the file you have. ✓ marks a file that is yours: identical, or the same weights with different file info.","muted"));
  data.versions.forEach(v=>{
   target.append(text("strong",v.name+(v.base_model?" · "+v.base_model:"")+(v.published_at?" · "+v.published_at.slice(0,10):""),"version-name"));
   v.files.forEach(f=>{
    const mine=f.exact||f.same_weights;
    const b=text("button",(f.exact?"✓ Your file: ":f.same_weights?"✓ Same weights as your file: ":"")+f.name+" · "+f.type+" · "+fmt(f.size_bytes),"repo-action"+(mine?" exact":""));b.type="button";
    b.addEventListener("click",async()=>{
     if(!mine&&!confirm("This is NOT the same file as yours ("+fmt(f.size_bytes)+" vs your "+fmt(m.size_bytes)+").\n\nOnly link it if your file is a conversion of this one (fp8, GGUF…) that you made yourself. Link anyway?"))return;
     b.disabled=true;
     try{const r=await apiRequest("/api/civitai/link","POST",{device:m.device,path:m.path,model_id:data.id,version_id:v.id,file_id:f.id});
      await refresh();showNotice((r.same_weights?"Linked (same weights): ":r.verified?"Linked (exact hash match): ":"Linked by hand: ")+m.name+" → "+data.name+" "+v.name);reopen();}
     catch(err){target.append(text("p",err.message,"muted"));b.disabled=false;}
    });
    target.append(b);
   });
  });
 }catch(err){target.replaceChildren(text("p",err.message,"muted"));}
}
const VERDICT_TEXT={exact:"✓ Contains your file (identical)",same_weights:"✓ Contains your file (same weights; only the file's info differs)",
 same_size:"≈ Has a file the same size as yours, but with different contents (an edited copy?). Not a safe match."};
// Civitai results judged against the user's file by fingerprint and size, so they never have to guess.
async function checkCivitai(q,target){
 const m=currentModel,data=await apiRequest("/api/civitai/check?"+new URLSearchParams({device:m.device,path:m.path,q}));
 target.replaceChildren();
 const found=data.models.flatMap(x=>x.files.filter(f=>f.verdict!=="same_size").map(f=>({x,f})));
 if(found.length){
  target.append(text("p","✓ Found your file on Civitai. Linking it is safe.","match-verified"));
  found.forEach(({x,f})=>{const b=text("button","Link to "+x.name+" — "+f.version_name+(f.base_model?" · "+f.base_model:""),"primary");b.type="button";
   b.addEventListener("click",async()=>{b.disabled=true;try{const r=await apiRequest("/api/civitai/link","POST",{device:m.device,path:m.path,model_id:x.id,version_id:f.version_id,file_id:f.file_id});
    await refresh();showNotice((r.same_weights?"Linked (same weights): ":"Linked (exact hash match): ")+m.name+" → "+x.name+" "+f.version_name);reopen();}catch(err){target.append(text("p",err.message,"muted"));b.disabled=false;}});
   target.append(b);});
 }else target.append(text("p",data.models.length?"✗ Your file isn't in these results. All "+data.checked_files+" files in "+data.models.length+" models were compared with yours by fingerprint and size, and none of them is your file. It may be a deleted version, a re-upload, or from another site. Try a different search, or press “Mark as local only”.":"No models found. Try a shorter name.","match-none"));
 data.models.forEach(x=>{
  const row=document.createElement("a");row.className="provider-result";row.href=x.url;row.target="_blank";row.rel="noopener noreferrer";
  row.append(text("strong",x.name),text("span",(VERDICT_TEXT[x.best]||"✗ Not your file"+(x.sizes.length?": its files are "+x.sizes.map(fmt).join(", ")+"; yours is "+fmt(m.size_bytes):""))+" · opens site ↗","model-sub"));
  target.append(row);
  if(!found.length){const b=text("button","Link anyway (only if I converted this model myself)","repo-action");b.type="button";const picker=text("div","","version-picker");b.addEventListener("click",()=>showCivitaiVersions(x,picker));target.append(b,picker);}
 });
}
async function searchProvider(){
 const provider=$("provider").value, q=$("provider-query").value.trim(), target=$("provider-results");
 target.replaceChildren();
 if(q.length<3){target.append(text("p","Enter at least 3 characters.","muted"));return;}
 const button=$("provider-search");button.disabled=true;target.append(text("p",provider==="civitai"?"Searching Civitai and comparing every file with yours…":"Searching public model listings…","muted"));
 try{
  if(provider==="civitai"){await checkCivitai(q,target);return;}
  const response=await fetch("/api/discovery/"+encodeURIComponent(provider)+"?q="+encodeURIComponent(q),{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});
  if(!response.ok)throw Error("Search unavailable (HTTP "+response.status+")");
  const payload=await response.json();target.replaceChildren();
  if(!payload.results.length){target.append(text("p","No candidates found. Try a shorter model name.","muted"));return;}
  payload.results.forEach(item=>{
   const a=document.createElement("a");a.className="provider-result";
   a.href=item.url;a.target="_blank";a.rel="noopener noreferrer";
   a.append(text("strong",item.name),text("span",item.provider+" · Unverified candidate · opens site ↗","model-sub"));
   target.append(a);
   if(item.provider==="civitai"){const button=text("button","Link to my file","repo-action");button.type="button";const picker=text("div","","version-picker");button.addEventListener("click",()=>showCivitaiVersions(item,picker));target.append(button,picker);}
   if(item.provider==="huggingface"){const button=text("button","Browse repository files","repo-action");button.type="button";button.addEventListener("click",()=>{$("repo-name").value=item.id;browseRepository();});target.append(button);}
  });
 }catch(err){target.replaceChildren(text("p",err.message,"muted"));}
 finally{button.disabled=false;}
}

$("provider-search").addEventListener("click",searchProvider);

let repoListing=null;
function selectedRemote(file){
 return selectedMatches.filter(s=>repoListing&&s.repository===repoListing.repository&&s.remote_path===file.path);
}
async function apiRequest(path,method="GET",body){
 const response=await fetch(path,{method,headers:{"X-ModelHarbor-Token":token,...(body?{"Content-Type":"application/json"}:{})},
  body:body?JSON.stringify(body):undefined,cache:"no-store"});
 if(!response.ok){let detail="HTTP "+response.status;try{const data=await response.json();if(typeof data.detail==="string")detail=data.detail;}catch(_){}
  throw Error(detail);}
 return response.json();
}
async function requestVerification(file,local,status,button,controls){
 button.disabled=true;status.textContent="Verification queued; the PC's agent will read the selected file…";
 try{
  const job=await apiRequest("/api/hash-jobs","POST",{
   device:local.device,path:local.path,repository:repoListing.repository,remote_path:file.path
  });
  async function poll(){
   if(!document.getElementById("detail-dialog").open)return;
   try{
    const current=await apiRequest("/api/hash-jobs/"+encodeURIComponent(job.id));
    if(current.state==="queued"||current.state==="running"){
     status.textContent=current.state==="running"?"Computing SHA-256 on the PC…":"Waiting for the PC's agent…";
     window.setTimeout(poll,5000);return;
    }
    if(current.state==="verified"){
     status.textContent="SHA-256 verified — exact bytes match. Confirm to associate this file.";
     const confirm=text("button","Select verified match","primary");confirm.type="button";
     controls.append(confirm);
     confirm.onclick=async()=>{
      confirm.disabled=true;
      try{
       const chosen=await apiRequest("/api/hash-jobs/"+encodeURIComponent(job.id)+"/select","POST");
       selectedMatches=selectedMatches.filter(s=>!(s.device===chosen.device&&s.path===chosen.path));
       selectedMatches.push(chosen);
       displayRepoFiles();
      }catch(err){status.textContent="Could not select match: "+err.message;confirm.disabled=false;}
     };
    }else{
     status.textContent=current.state==="mismatch"?"SHA-256 mismatch. These files are not identical.":current.state==="stale"?"Local file inventory changed. Refresh and verify again.":"Verification failed: "+(current.error||"Unknown error");
     button.disabled=false;
    }
   }catch(err){status.textContent="Status lookup failed: "+err.message;button.disabled=false;}
  }
  poll();
 }catch(err){status.textContent="Verification could not start: "+err.message;button.disabled=false;}
}
function repoFileRow(file){
 const card=text("div","","repo-file");
 card.append(text("strong",file.name,"repo-filename"),
   text("span",file.path+" · "+fmt(file.size_bytes),"model-sub"));
 const chosen=selectedRemote(file);
 if(chosen.length){
  card.append(text("span","✓ Selected exact match: "+chosen.map(s=>s.device+" · "+s.path).join(", "),"match-verified"));
 }
 const byName=models.filter(m=>m.name.toLowerCase()===file.name.toLowerCase()&&m.device.endsWith(":comfyui"));
 const sameSize=models.filter(m=>m.device.endsWith(":comfyui")&&Number(m.size_bytes)===file.size_bytes);
 const candidates=sameSize.sort((a,b)=>(Number(b.name.toLowerCase()===file.name.toLowerCase())-Number(a.name.toLowerCase()===file.name.toLowerCase()))||a.name.localeCompare(b.name));
 card.append(text("span",byName.some(m=>m.size_bytes===file.size_bytes)?"Filename + size match (unverified)":byName.length?"Filename match (size differs)":candidates.length?"Other local files have the same size; select one to verify":"No similar local file",candidates.length?"match-possible":"model-sub"));
 if(file.sha256)card.append(text("span","Remote SHA-256 available","model-sub"));
 const link=document.createElement("a");link.href=file.url;link.target="_blank";link.rel="noopener noreferrer";
 link.textContent="View file ↗";link.className="repo-link";card.append(link);
 if(candidates.length && file.sha256){
  const controls=text("div","","verify-controls");
  const picker=document.createElement("select");picker.setAttribute("aria-label","Choose local file to verify");
  candidates.slice(0,300).forEach((m,i)=>picker.add(new Option(m.name+" · "+m.device+" · "+m.path,i)));
  const verify=text("button","Verify SHA-256","primary");verify.type="button";
  const status=text("p","","muted");status.setAttribute("role","status");
  verify.onclick=()=>requestVerification(file,candidates[Number(picker.value)],status,verify,controls);
  controls.append(picker,verify);card.append(controls,status);
 }else if(candidates.length && !file.sha256){
  card.append(text("span","No authoritative remote SHA-256 available. Verification unavailable.","model-sub"));
 }
 return card;
}
function displayRepoFiles(){
 const area=$("repo-files");area.replaceChildren();
 if(!repoListing)return;
 const term=$("repo-filter").value.trim().toLowerCase().replace(/[_-]/g," ");
 const list=repoListing.files.filter(f=>f.path.toLowerCase().replace(/[_-]/g," ").includes(term));
 $("repo-status").textContent=list.length+" files shown of "+repoListing.files.length+" model files"+(repoListing.complete?"":" · Partial listing; some files may be missing");
 if(!list.length)area.append(text("p","No model files found for this search.","muted"));
 for(const file of list.slice(0,500))area.append(repoFileRow(file));
 if(list.length>500)area.append(text("p","Showing first 500 matching files. Narrow the filter.","muted"));
}
async function browseRepository(){
 const repo=$("repo-name").value.trim();
 if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$/.test(repo)||repo.includes("..")){
  $("repo-status").textContent="Enter a valid owner/repository name.";return;
 }
 const btn=$("repo-browse");btn.disabled=true;repoListing=null;$("repo-files").replaceChildren();$("repo-status").textContent="Loading repository file metadata…";
 try{
  const response=await fetch("/api/huggingface/repository?repo="+encodeURIComponent(repo),{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});
  if(!response.ok)throw Error("Repository lookup failed (HTTP "+response.status+")");
  repoListing=await response.json();displayRepoFiles();
 }catch(err){$("repo-status").textContent=err.message;}
 finally{btn.disabled=false;}
}
$("repo-browse").addEventListener("click",browseRepository);
$("repo-filter").addEventListener("input",displayRepoFiles);
// LLM server models have no hash; they are linked by name and size instead.
const isLlmServer=m=>m.kind.startsWith("lemonade/")||m.kind.startsWith("lmstudio/");
const isUnmatched=m=>(!!m.sha256||isLlmServer(m))&&!m.source&&!m.hard_link&&!m.kind.startsWith("ollama/")&&!m.match_ignored;
function modelRow(m){const b=text("button","", "model");b.type="button";b.append(text("span","◈","glyph"));const info=text("span","","model-text");info.append(text("div",m.name,"model-name"),text("div",m.kind+" · "+m.device+" · "+fmt(m.size_bytes)+(m.possible_duplicate?(m.duplicate_verified?" · Duplicate":" · Possible duplicate"):"")+(m.hard_link?(isLlmServer(m)?" · Alias":" · Hard link"):"")+(m.source?" · ✓ "+providerName(m.source.provider):"")+(m.update?" · ⬆ Update":"")+(isUnmatched(m)?" · Unmatched":"")+(m.match_ignored?" · Local only":""),"model-sub"));b.append(info,text("span","›","chevron"));b.addEventListener("click",()=>details(m));return b;}
function fill(target,items,empty){target.replaceChildren();if(!items.length)target.append(text("p",empty,"muted"));else items.forEach(m=>target.append(modelRow(m)));}
const providerName=p=>({civitai:"Civitai",huggingface:"Hugging Face",modelscope:"ModelScope",ollama:"Ollama",lemonade:"Lemonade",lmstudio:"LM Studio"})[p]||p;
function linkRow(dl,label,caption,url){const a=document.createElement("a");a.href=url;a.target="_blank";a.rel="noopener noreferrer";a.textContent=caption;const dd=document.createElement("dd");dd.append(a);dl.append(text("dt",label),dd);}
function downloadStatus(j){
 if(!j)return "Saved next to the current file. Once its SHA-256 matches the published file, the old version is deleted.";
 if(j.state==="queued")return "Waiting for the PC's agent…";
 if(j.state==="running")return j.provider==="ollama"?"Ollama is pulling the newer build…":"Downloading "+fmt(j.bytes_done)+(j.size_bytes?" of "+fmt(j.size_bytes):"")+"…";
 if(j.state==="done"&&j.provider==="ollama")return "Pulled the newer build.";
 if(j.state==="done"){
  const saved="Saved and verified: "+j.result_path+". ";
  if(j.cleanup==="queued"||j.cleanup==="running")return saved+"Re-checking both files, then deleting the old version…";
  if(j.cleanup==="done")return saved+"The old version was deleted. Up to date.";
  if(j.cleanup==="failed")return saved+"The old version was not deleted: "+(j.cleanup_error||"unknown error");
  return saved+"The old version is still there.";
 }
 return "Download failed: "+(j.error||"unknown error");
}
function renderDelete(m){
 const box=$("delete-box");box.replaceChildren();
 if(!m.device.endsWith(":comfyui")||!m.sha256)return;
 const pc=m.device.replace(/:comfyui$/,""),links=m.file_id?models.filter(x=>x.device===m.device&&x.file_id===m.file_id):[m];
 const b=text("button","Delete from "+pc+"…","repo-action danger");b.type="button";const status=text("p","","muted");box.append(b,status);
 b.addEventListener("click",async()=>{
  if(!confirm("Permanently delete this file from "+pc+"?\n\n"+links.map(x=>x.path).join("\n")+(links.length>1?"\n\nThese paths are the same file (hard links), so all of them go.":"")+"\n\nIts preview images and .metadata.json go too. The PC re-checks the file's SHA-256 first. This cannot be undone."))return;
  b.disabled=true;
  try{
   let job=await apiRequest("/api/models/delete","POST",{device:m.device,path:m.path});
   while(job.state==="queued"||job.state==="running"){
    status.textContent=job.state==="queued"?"Waiting for the PC's background task (up to 20 seconds)…":"Checking the file and deleting…";
    await new Promise(r=>setTimeout(r,3000));job=await apiRequest("/api/deletes/"+encodeURIComponent(job.id));}
   if(job.state==="failed"){status.textContent="Not deleted: "+job.error;b.disabled=false;return;}
   $("detail-dialog").close();await refresh();showNotice("Deleted "+m.name+" from "+pc);
  }catch(err){status.textContent=err.message;b.disabled=false;}
 });
}
async function renderDownload(m){
 const box=$("download-box");box.replaceChildren();if(!m.update)return;
 if(m.update.provider==="lemonade"){box.append(text("p","Lemonade keeps its own copy of this model. Update it from Lemonade, which downloads the newer file from Hugging Face.","muted"));return;}
 const button=text("button","Download update","primary");button.type="button";const status=text("p","","muted");box.append(button,status);
 if(m.source&&m.source.provider==="civitai"&&!m.source.verified)box.prepend(text("p","Your file differs from the published one (a converted copy, for example). The update downloads the original format: convert it again before deleting your old file.","muted"));
 let job=null,wasActive=false;
 const remove=text("button","Delete old version","repo-action danger");remove.type="button";remove.hidden=true;box.append(remove);
 const show=j=>{job=j||job;status.textContent=downloadStatus(job);
  const cleaning=job&&(job.cleanup==="queued"||job.cleanup==="running");
  const active=job&&(job.state==="queued"||job.state==="running")||cleaning;
  button.disabled=!!active;button.hidden=!!job&&job.state==="done";  // nothing left to download
  // Shown only when the automatic delete was skipped (a hand-linked file) or failed.
  remove.hidden=!(job&&job.state==="done"&&job.provider!=="ollama"&&(!job.cleanup||job.cleanup==="failed"));
  if(wasActive&&!active)pollAgents();  // the agent rescans now; the dashboard reloads when that finishes
  wasActive=!!active;
  if(active)setTimeout(poll,3000);};
 remove.addEventListener("click",async()=>{
  if(!confirm("Permanently delete the old version?\n\n"+m.path+"\n\nThe new version passed its SHA-256 check:\n"+job.result_path+"\n\nBoth files are re-checked first. This cannot be undone."))return;
  remove.disabled=true;
  try{await apiRequest("/api/downloads/"+encodeURIComponent(job.id)+"/delete-old","POST");show({...job,cleanup:"queued",cleanup_error:null});}
  catch(err){status.textContent=err.message;remove.disabled=false;}
 });
 async function poll(){if(!$("detail-dialog").open||$("detail-title").textContent!==m.name)return;try{show((await apiRequest("/api/downloads")).find(x=>x.id===job.id));}catch(err){status.textContent=err.message;}}
 button.addEventListener("click",async()=>{button.disabled=true;try{show(await apiRequest("/api/downloads","POST",{device:m.device,path:m.path}));}catch(err){status.textContent=err.message;button.disabled=false;}});
 try{show((await apiRequest("/api/downloads")).find(x=>x.device===m.device&&x.path===m.path));}catch(_){show(null);}
}
function renderLinkTools(m){
 const box=$("link-box");box.replaceChildren();
 if(isUnmatched(m)||m.match_ignored){
  // Own LoRAs and merges are on no site; marking them keeps the Unmatched list to real work.
  const b=text("button",m.match_ignored?"Show in Unmatched again":"Mark as local only (not on any site)","repo-action");b.type="button";
  b.addEventListener("click",async()=>{b.disabled=true;try{await apiRequest("/api/match-ignore","POST",{device:m.device,path:m.path,ignored:!m.match_ignored});await refresh();showNotice((m.match_ignored?"Back on the Unmatched list: ":"Marked local only: ")+m.name);reopen();}catch(err){box.append(text("p",err.message,"muted"));b.disabled=false;}});
  box.append(b);return;
 }
 if(m.source&&m.source.provider==="huggingface"&&!m.source.reported_by){
  const b=text("button","Unlink from Hugging Face","repo-action");b.type="button";
  b.addEventListener("click",async()=>{b.disabled=true;try{await apiRequest("/api/huggingface/unlink","POST",{device:m.device,path:m.path});await refresh();showNotice("Unlinked "+m.name);reopen();}catch(err){box.append(text("p",err.message,"muted"));b.disabled=false;}});
  box.append(b);return;
 }
 if(!m.source||m.source.provider!=="civitai")return;
 const b=text("button",m.source.verified?"Unlink and re-check by hash":"Unlink from Civitai","repo-action");b.type="button";
 b.addEventListener("click",async()=>{b.disabled=true;try{await apiRequest("/api/civitai/unlink","POST",{device:m.device,path:m.path});await refresh();showNotice("Unlinked "+m.name);reopen();}catch(err){box.append(text("p",err.message,"muted"));b.disabled=false;}});
 box.append(b);
}
const CONVERSION_WORDS=/\b(fp8|fp16|fp32|bf16|fp4|nvfp4|mxfp4|e4m3fn|e5m2|scaled|int8|int4|nf4|svdq|gguf|q[2-8](?: k)?(?: [sml])?|q8 0|q4 [01]|q5 [01])\b/gi;
function copyToClipboard(str,btn,doneText="Copied!"){
 if(!str)return;
 navigator.clipboard.writeText(str).then(()=>{
  const orig=btn.textContent;btn.textContent=doneText;btn.classList.add("copied");
  setTimeout(()=>{btn.textContent=orig;btn.classList.remove("copied");},1800);
 }).catch(()=>{});
}
function getTriggerWords(m){
 if(m.source&&m.source.trained_words&&m.source.trained_words.length)return m.source.trained_words;
 if(m.local_meta&&m.local_meta.trained_words&&m.local_meta.trained_words.length)return m.local_meta.trained_words;
 if(m.trigger_words){
  if(Array.isArray(m.trigger_words))return m.trigger_words;
  try{const parsed=JSON.parse(m.trigger_words);if(Array.isArray(parsed))return parsed;}catch(_){}
 }
 return [];
}
function getInstalledFamilies(){
 const res=new Set();
 models.forEach(m=>{
  if(m.kind==="checkpoint"||m.kind==="diffusion"||m.kind.startsWith("ollama/")||m.kind.startsWith("lemonade/")){
   if(m.family_guess&&m.family_guess!=="Unknown")res.add(m.family_guess.toLowerCase());
  }
 });
 return res;
}
function hasBaseModelFor(fam){
 if(!fam||fam==="Unknown")return true;
 const f=fam.toLowerCase().replace(/[^a-z0-9]/g,"");
 for(const inst of getInstalledFamilies()){
  const i=inst.replace(/[^a-z0-9]/g,"");
  if(f.includes(i)||i.includes(f))return true;
 }
 return false;
}
function isFamilyCompatible(fam){return !fam||hasBaseModelFor(fam);}
function renderCompanionWarning(m){
 const box=$("companion-warning");box.replaceChildren();box.hidden=true;
 if(m.kind!=="lora"&&m.kind!=="locon"&&m.kind!=="controlnet")return;
 const fam=(m.source&&m.source.base_model)||m.family_guess;
 if(!fam||fam==="Unknown"||hasBaseModelFor(fam))return;
 box.hidden=false;
 box.append(text("span","⚠️ Requires a "+fam+" base model. You don't have a "+fam+" checkpoint or diffusion model installed yet."));
 const findBtn=text("button","Find "+fam+" checkpoints ↗");findBtn.type="button";
 findBtn.onclick=()=>{
  $("detail-dialog").close();switchView("browse");$("browse-provider").value="civitai";$("browse-type").value="Checkpoint";$("browse-base").value="";$("browse-q").value=fam;browseSearch(false);
 };
 box.append(findBtn);
}
// Suggestions also include files that only share a name or size; linking those says "this is my file" when it isn't.
const SURE=new Set(["exact","same_weights"]);
function verdictLabel(c){
 const v=c.verdict||c.match_type;
 if(v==="exact")return "✓ Exact SHA-256 match";
 if(v==="same_weights")return c.provider==="civitai"?"✓ Same model weights":"Same name and size (not hash-checked)";
 if(v==="converted")return "Looks like your conversion of this file (same name, different precision)";
 if(v==="same_size")return "Same size only (different contents)";
 return "Name match only";
}
function confirmLink(c,m){
 const v=c.verdict||c.match_type;
 if(v==="exact"||(v==="same_weights"&&c.provider==="civitai"))return true;
 return confirm("This is not a verified match ("+verdictLabel(c)+").\n\n"+m.name+" → "+(c.model_name||c.name||c.repository)+"\n\nOnly link it if you know your file is this model (for example, your own conversion of it). Link anyway?");
}
async function renderSuggestedMatches(m){
 const box=$("suggest-matches-box");box.replaceChildren();box.hidden=true;
 // Local-only files named like a conversion (int8, fp8…) are often the user's own copy of a published model.
 if(!isUnmatched(m)&&!(m.match_ignored&&!m.source&&COMPACT_NAME.test(m.name)))return;
 box.hidden=false;box.append(text("p","Searching for match candidates on Civitai & Hugging Face…","muted"));
 try{
  const data=await apiRequest("/api/models/suggest-match?"+new URLSearchParams({device:m.device,path:m.path}));
  box.replaceChildren();
  const cv=data.civitai||[],hf=data.huggingface||[];
  if(!cv.length&&!hf.length){
   box.replaceChildren(text("p","No automatic online match found for this file. You can search manually below, or mark it as local only.","muted"));
   const localBtn=text("button","Mark as local only (not on any site)","repo-action");localBtn.type="button";
   localBtn.onclick=async()=>{
    localBtn.disabled=true;
    try{
     await apiRequest("/api/match-ignore","POST",{device:m.device,path:m.path,ignored:true});
     await refresh();showNotice("Marked local only: "+m.name);reopen();
    }catch(err){localBtn.disabled=false;}
   };
   box.append(localBtn);
   return;
  }
  const h4=document.createElement("h4");h4.textContent="⚡ Suggested online matches ("+(cv.length+hf.length)+")";box.append(h4);
  cv.forEach(c=>{
   const card=text("div","","suggest-match-card"),info=text("div","","suggest-match-info");
   info.append(text("strong","Civitai · "+c.name+" ("+c.version_name+")"),text("small",verdictLabel(c)+(c.base_model?" · "+c.base_model:"")));
   const linkBtn=text("button","Link to this model","primary");linkBtn.type="button";
   linkBtn.onclick=async()=>{
    if(!confirmLink(c,m))return;
    linkBtn.disabled=true;
    try{
     const r=await apiRequest("/api/civitai/link","POST",{device:m.device,path:m.path,model_id:c.model_id,version_id:c.version_id,file_id:c.file_id});
     await refresh();showNotice((r.same_weights?"Linked (same weights): ":r.verified?"Linked (exact hash match): ":"Linked: ")+m.name+" → Civitai "+c.name+" "+c.version_name);reopen();
    }catch(err){showNotice("Link failed: "+err.message);linkBtn.disabled=false;}
   };
   card.append(info,linkBtn);box.append(card);
  });
  hf.forEach(h=>{
   const card=text("div","","suggest-match-card"),info=text("div","","suggest-match-info");
   info.append(text("strong","Hugging Face · "+h.repository+" / "+h.remote_path),text("small",verdictLabel(h)+(h.base_model?" · "+h.base_model:"")));
   const linkBtn=text("button","Link to this model","primary");linkBtn.type="button";
   linkBtn.onclick=async()=>{
    if(!confirmLink(h,m))return;
    linkBtn.disabled=true;
    try{
     await apiRequest("/api/huggingface/link","POST",{device:m.device,path:m.path,repository:h.repository,remote_path:h.remote_path,sha256:h.sha256,base_model:h.base_model,trigger_words:h.trigger_words});
     await refresh();showNotice("Linked: "+m.name+" → Hugging Face "+h.repository);reopen();
    }catch(err){showNotice("Link failed: "+err.message);linkBtn.disabled=false;}
   };
   card.append(info,linkBtn);box.append(card);
  });
 }catch(_){box.hidden=true;}
}
async function queueLmStudioAction(model,action,instanceId,contextLength,status){
 status.textContent="Sending request to the PC's agent…";
 try{
  let job=await apiRequest("/api/lmstudio/actions","POST",{device:model.device,path:model.path,action,instance_id:instanceId||null,context_length:contextLength||null});
  const poll=async()=>{
   status.textContent=job.state==="queued"?"Waiting for the PC's agent…":"LM Studio is processing the request…";
   if(job.state==="queued"||job.state==="running"){
    setTimeout(async()=>{try{job=(await apiRequest("/api/downloads")).find(x=>x.id===job.id)||job;poll();}catch(err){status.textContent=err.message;}},2500);return;
   }
   if(job.state==="failed"){status.textContent="LM Studio request failed: "+(job.error||"unknown error");return;}
   status.textContent=action==="load"?"Model loaded in LM Studio.":"Model unloaded from LM Studio.";
   await refresh();const fresh=models.find(x=>x.device===model.device&&x.path===model.path);if(fresh&&$("detail-dialog").open)details(fresh);
  };
  poll();
 }catch(err){status.textContent=err.message;}
}
function renderLmStudio(m){
 const box=$("runtime-box");box.replaceChildren();box.hidden=!m.kind.startsWith("lmstudio/");if(box.hidden)return;
 const meta=m.runtime_meta||{},quant=meta.quantization||{},loaded=Array.isArray(meta.loaded_instances)?meta.loaded_instances:[],runtimeAgent=agents.find(a=>a.device===m.device.replace(/:lmstudio$/, "")),runtime=runtimeAgent?.lmstudio;
 const heading=text("h3","LM Studio","section-title");box.append(heading);
 box.append(text("p",[meta.architecture,meta.format,quant.name,meta.params_string,meta.max_context_length?"Max context "+Number(meta.max_context_length).toLocaleString():""].filter(Boolean).join(" · ")||"Runtime details unavailable","muted"));
 const status=text("p",runtime&&runtime.status==="unavailable"?(runtime.error||"LM Studio is unavailable"):"","muted");box.append(status);
 if(!runtimeAgent||!runtimeAgent.online||!runtime||runtime.status!=="connected"){box.append(text("p","LM Studio actions are unavailable until the PC's agent reconnects.","muted"));return;}
 if(loaded.length){
  loaded.forEach(instance=>{const row=text("div","","runtime-action-row");row.append(text("span","Loaded"+(instance.context_length?" · "+Number(instance.context_length).toLocaleString()+" context":""),"model-sub"));const button=text("button","Unload","repo-action");button.type="button";button.addEventListener("click",()=>queueLmStudioAction(m,"unload",instance.id,null,status));row.append(button);box.append(row);});
 }else{
  const max=Number(meta.max_context_length)||0;
  if(!max){box.append(text("p","LM Studio did not report a maximum context length, so ModelHarbor cannot safely load this model.","muted"));return;}
  const row=text("div","","runtime-action-row"),label=document.createElement("label");label.textContent="Context length";const input=document.createElement("input");input.type="number";input.min="1";input.max=String(max);input.step="1";input.value=String(Math.min(8192,max));input.setAttribute("aria-label","Context length");const button=text("button","Load in LM Studio","primary");button.type="button";button.addEventListener("click",()=>{const value=Number(input.value);if(!Number.isInteger(value)||value<1||value>max){status.textContent="Enter a context length from 1 to "+max.toLocaleString()+".";return;}button.disabled=true;queueLmStudioAction(m,"load",null,value,status).finally(()=>{button.disabled=false;});});row.append(label,input,button);box.append(row);
 }
}
function details(m){
 currentModel=m;$("detail-title").textContent=m.name;renderCompanionWarning(m);renderSuggestedMatches(m);
 const dl=$("detail-fields");dl.replaceChildren();
 const triggers=getTriggerWords(m),stem=m.name.replace(/\.[^.]+$/,"");
 if(m.kind==="lora"||m.kind==="locon"){
  const dt=text("dt","LoRA prompt"),dd=document.createElement("dd");
  const loraTag=`<lora:${stem}:1.0>`;
  const tagBtn=text("button","📋 Copy "+loraTag,"copy-btn");tagBtn.type="button";tagBtn.onclick=()=>copyToClipboard(loraTag,tagBtn);dd.append(tagBtn);
  if(triggers.length){
   const fullPrompt=`${loraTag}, ${triggers.join(", ")}`;
   const fullBtn=text("button","📋 Copy tag + triggers","copy-btn");fullBtn.type="button";fullBtn.onclick=()=>copyToClipboard(fullPrompt,fullBtn);dd.append(fullBtn);
  }
  dl.append(dt,dd);
 }
 const baseModelVal=(m.source&&m.source.base_model)||m.base_model_guess||(m.local_meta&&m.local_meta.base_model);
 const runtimeMeta=m.runtime_meta||{},runtimeQuant=runtimeMeta.quantization||{};
 for(const [k,v] of Object.entries({
  Type:m.kind,Device:m.device,Size:fmt(m.size_bytes),Family:(m.family_guess||"Unknown")+(m.family_verified?" (from source)":" (unverified)"),
  ...(m.source?{Source:providerName(m.source.provider)+" · "+m.source.model_name+(m.source.version_name?" — "+m.source.version_name:"")+(m.source.provider==="civitai"?(m.source.weights_match?" (same weights; file info differs)":m.source.verified?" (exact hash match)":" (linked by hand, hash differs)"):m.source.reported_by?" (reported by "+providerName(m.source.provider)+")":isLlmServer(m)?" (linked by hand)":"")}:{"Source":isLlmServer(m)?"Not linked to Hugging Face yet":!m.sha256?"Waiting for the Windows agent to hash this file":m.kind.startsWith("ollama/")?"Not on the Ollama registry":m.source_checked?"Not found on Civitai":"Checking Civitai…"}),
  ...(baseModelVal?{"Base model":baseModelVal}:{}),
  ...(m.kind.startsWith("lmstudio/")?{"LM Studio model key":runtimeMeta.model_key||m.checkpoint,"Architecture":runtimeMeta.architecture||"Unknown","Format":runtimeMeta.format||"Unknown","Quantization":runtimeQuant.name||"Unknown","Maximum context":runtimeMeta.max_context_length?Number(runtimeMeta.max_context_length).toLocaleString():"Unknown"}:{}),
  ...(m.source&&m.source.weight?{"Recommended weight":m.source.weight+" (median in the creator's example images)"}:{}),
  ...(m.update?{Update:m.update.version_name}:{}),
  ...(m.checkpoint?{Checkpoint:m.checkpoint}:{}),
  Duplicates:m.possible_duplicate?(m.duplicate_verified?m.duplicate_group_size+" copies with the same SHA-256 (identical files)":"Same name and size — not hashed yet"):m.sha256?"No other file with this SHA-256":"No matching name and size",
  ...(m.hard_link&&isLlmServer(m)?{Alias:"Another runtime name for "+(m.checkpoint||"the same file")+"; counted once"}:{}),Folder:m.path.replace(/[\\/][^\\/]+$/,""),Path:m.path,
  ...(m.sha256?{[m.kind.startsWith("ollama/")?"Digest":"SHA-256"]:m.sha256}:{}),
  ...(selectedMatches.some(s=>s.device===m.device&&s.path===m.path)?{"Verified source":selectedMatches.filter(s=>s.device===m.device&&s.path===m.path).map(s=>"Hugging Face · "+s.repository+" / "+s.remote_path).join(", ")}:{})
 })){
  dl.append(text("dt",k),text("dd",v));
 }
 if(triggers.length){
  const dt=text("dt","Trigger words"),dd=document.createElement("dd");
  const copyAll=text("button","📋 Copy all","copy-btn");copyAll.type="button";copyAll.onclick=()=>copyToClipboard(triggers.join(", "),copyAll);dd.append(copyAll);
  const chips=text("div","","trigger-chips");
  triggers.forEach(w=>{
   const chip=text("span",w,"trigger-chip");chip.title="Click to copy trigger phrase";chip.onclick=()=>copyToClipboard(w,chip,"✓ "+w);chips.append(chip);
  });
  dd.append(chips);dl.append(dt,dd);
 }
 if(m.source)linkRow(dl,"Source page","Open on "+providerName(m.source.provider)+" ↗",m.source.url);
 if(m.update)linkRow(dl,"New version","Open "+m.update.version_name+" ↗",m.update.url);
 const cleanName = m.name.replace(/\.(safetensors|ckpt|gguf|pt|pth|bin|onnx)$/i,"").replace(/[_-]+/g," ").replace(CONVERSION_WORDS,"").replace(/\s+/g," ").trim();
 let prefill = cleanName;
 if(!prefill || ["model", "weights", "diffusion", "unet", "clip", "tagger"].includes(prefill.toLowerCase())){
  const parts = m.path.replace(/\\/g,"/").split("/");
  if(parts.length >= 2){
   prefill = parts[parts.length - 2].replace(/\.(safetensors|ckpt|gguf|pt|pth|bin|onnx)$/i,"").replace(/[_-]+/g," ").replace(CONVERSION_WORDS,"").replace(/\s+/g," ").trim();
  }
 }
 // Searching only helps files that are not identified yet, or linked by hand to a different file.
 document.querySelector("#detail-dialog .discovery").hidden=!!m.source&&m.source.verified!==false;
 $("provider-query").value = (prefill || m.name).slice(0, 120);
 $("provider-results").replaceChildren();$("repo-files").replaceChildren();$("repo-status").textContent="";$("repo-filter").value="";
 renderDownload(m);renderLinkTools(m);renderDelete(m);renderLmStudio(m);
 if(!$("detail-dialog").open)$("detail-dialog").showModal();
}
function options(id,values,label){const el=$(id),old=el.value;el.replaceChildren(new Option(label,""));values.forEach(v=>el.add(new Option(v,v)));el.value=values.includes(old)?old:"";}
const ago=t=>{if(!t)return "never";const s=Math.max(0,Math.round(Date.now()/1000-t));return s<60?s+" s ago":s<3600?Math.round(s/60)+" min ago":s<86400?Math.round(s/3600)+" h ago":new Date(t*1000).toLocaleString();};
const shortPath=p=>(p||"").split(/[\\/]/).slice(-2).join("\\");
function agentStatusText(a){
 const s=a.status;const busy=s&&["scanning","hashing","uploading"].includes(s.phase);
 if(a.scan_requested&&!busy)return a.worker_running?"Scan requested — starting within about 20 seconds…":"Scan requested — waiting for the PC's background task to come online.";
 if(!s)return "No scan reported yet.";
 if(s.phase==="scanning")return "Scanning folders… "+shortPath(s.current);
 if(s.phase==="hashing")return "Hashing new or changed files: "+s.done+" of "+s.total+" · "+fmt(s.done_bytes)+" of "+fmt(s.total_bytes)+(s.current?" · "+shortPath(s.current):"");
 if(s.phase==="uploading")return "Uploading inventory ("+s.total+" models)…";
 if(s.phase==="error")return "Last scan failed "+ago(s.updated_at)+": "+(s.message||"unknown error");
 return "Up to date · "+(s.message||"scan finished")+" · "+ago(s.updated_at);
}
function renderAgents(){
 const list=$("agent-list");list.replaceChildren();
 if(!agents.length){list.append(text("p","No PC agent has reported yet.","muted"));return;}
 agents.forEach(a=>{
  const card=text("div","","agent-card");const s=a.status;const busy=s&&["scanning","hashing","uploading"].includes(s.phase);
  card.append(text("strong",a.device),text("span",a.online?"● Online":"● Offline",a.online?"agent-online":"agent-offline"));
  card.append(text("div",agentStatusText(a),"agent-status"+(s&&s.phase==="error"?" agent-error":"")));
  if(s&&s.phase==="hashing"&&s.total_bytes){const bar=text("div","","bar");const inner=text("span");inner.style.width=Math.min(100,s.done_bytes/s.total_bytes*100)+"%";bar.append(inner);card.append(bar);}
  if(a.hardware)card.append(text("div",[a.hardware.gpu,a.hardware.vram_bytes?Math.round(a.hardware.vram_bytes/2**30)+" GB VRAM":"",a.hardware.ram_bytes?Math.round(a.hardware.ram_bytes/2**30)+" GB RAM":""].filter(Boolean).join(" · "),"model-sub"));
  card.append(text("div","ComfyUI: "+a.comfyui_count+(a.ollama_count!=null?" · Ollama: "+a.ollama_count:"")+(a.lemonade_count!=null?" · Lemonade: "+a.lemonade_count:"")+(a.lmstudio?.status==="connected"?" · LM Studio: "+a.lmstudio_count:"")+" · last full sync "+ago(a.last_seen),"model-sub"));
  const lm=a.lmstudio;
  if(lm?.status==="connected")card.append(text("div","LM Studio · "+(lm.status||"unknown")+(lm.host?" ("+lm.host+")":"")+(lm.error?" · "+lm.error:"")+([lm.gpu,lm.vram_bytes?Math.round(lm.vram_bytes/2**30)+" GB VRAM":"",lm.ram_bytes?Math.round(lm.ram_bytes/2**30)+" GB RAM":""].filter(Boolean).length?" · "+[lm.gpu,lm.vram_bytes?Math.round(lm.vram_bytes/2**30)+" GB VRAM":"",lm.ram_bytes?Math.round(lm.ram_bytes/2**30)+" GB RAM":""].filter(Boolean).join(" · "):""),"model-sub"));
  const lem=a.lemonade;
  if(lem&&a.lemonade_count!=null)card.append(text("div","Lemonade PC"+(lem.host?" ("+lem.host+")":"")+": "+[lem.gpu,lem.vram_bytes?Math.round(lem.vram_bytes/2**30)+" GB VRAM":"",lem.ram_bytes?Math.round(lem.ram_bytes/2**30)+" GB RAM":"",lem.free_bytes!=null?fmt(lem.free_bytes)+" free":""].filter(Boolean).join(" · "),"model-sub"));
  card.append(text("div",a.worker_running?"Background task running (checks for scan requests and downloads)":busy?"Background task paused while a scan runs"+(a.worker_seen?" · last seen "+ago(a.worker_seen):"")+" (it checks for jobs again when the scan ends)":"Background task not running"+(a.worker_seen?" · last seen "+ago(a.worker_seen):"")+". Start “ModelHarbor Hash Worker” in Task Scheduler.","model-sub"));
  const button=text("button",busy?"Scanning…":a.scan_requested?"Scan requested":"Scan now","repo-action");button.type="button";button.disabled=!!busy||a.scan_requested;
  button.addEventListener("click",async()=>{button.disabled=true;try{await apiRequest("/api/agents/"+encodeURIComponent(a.device)+"/scan","POST");await pollAgents();}catch(err){showNotice(err.message);button.disabled=false;}});
  card.append(button);
  if(!a.online){
   const remove=text("button","Remove device","repo-action danger");remove.type="button";
   remove.addEventListener("click",async()=>{
    if(!confirm("Remove "+a.device+" from ModelHarbor?\n\nThis removes all of its model inventory and related jobs from the app. Model files on the computer are not deleted.\n\nIf its agent reconnects, the device will appear again."))return;
    remove.disabled=true;
    try{await apiRequest("/api/agents/"+encodeURIComponent(a.device),"DELETE");await refresh();}
    catch(err){showNotice(err.message);remove.disabled=false;}
   });
   card.append(remove);
  }
  list.append(card);
 });
}
let agentsBusy=false;
async function pollAgents(){
 try{const wasBusy=agentsBusy;agents=await apiRequest("/api/agents");agentsBusy=agents.some(a=>a.scan_requested||a.status&&["scanning","hashing","uploading"].includes(a.status.phase));renderAgents();
  if(wasBusy&&!agentsBusy)refresh();  // a scan just finished: reload the inventory
 }catch(_){}
}
setInterval(()=>{if(token&&!document.hidden&&(view==="devices"||agentsBusy))pollAgents();},4000);
let browseCursor=null,browseJobs=[];
const browseAgent=()=>$("browse-agent").value||(agents.find(a=>a.online)||agents[0]||{}).device;
function fillBrowseAgents(){
 // Only ask which PC to download to when there is a choice.
 const select=$("browse-agent"),current=select.value;select.replaceChildren(...agents.map(a=>new Option("Download to "+a.device+(a.online?"":" (offline)"),a.device)));
 if(agents.some(a=>a.device===current))select.value=current;select.hidden=agents.length<2;
}
function newDownloadStatus(j){
 if(j.state==="queued")return "Waiting for the PC's agent…";
 if(j.state==="running"&&j.provider==="ollama")return "Ollama is pulling "+j.file_name+"…";
 if(j.state==="running"&&j.provider.startsWith("lmstudio-"))return "LM Studio is processing a "+j.provider.slice("lmstudio-".length)+" request…";
 if(j.state==="running"&&j.provider==="lmstudio")return "LM Studio is downloading "+(j.bytes_done?fmt(j.bytes_done)+(j.size_bytes?" of "+fmt(j.size_bytes):"")+" ":"")+j.file_name+"…";
 if(j.state==="running"&&j.provider==="lemonade")return "Lemonade is downloading "+(j.bytes_done?fmt(j.bytes_done)+(j.size_bytes?" of "+fmt(j.size_bytes):"")+" ":"")+"then loading "+j.file_name+"…";
 if(j.state==="running")return "Downloading "+fmt(j.bytes_done)+(j.size_bytes?" of "+fmt(j.size_bytes):"")+"…";
 if(j.state==="done"&&j.provider==="ollama")return "✓ In Ollama as "+j.file_name;
 if(j.state==="done"&&j.provider==="lemonade")return "✓ In Lemonade as "+j.file_name;
 if(j.state==="done"&&j.provider==="lmstudio")return "✓ Downloaded by LM Studio (not hash verified)";
 if(j.state==="done"&&j.provider.startsWith("lmstudio-"))return "✓ LM Studio "+j.provider.slice("lmstudio-".length)+" request finished";
 if(j.state==="done")return "✓ Downloaded and verified: "+shortPath(j.result_path);
 return "Download failed: "+(j.error||"unknown error");
}
function isOllamaCompatiblePath(path){
 const filename=(path||"").split("/").pop()||"";
 const stem=filename.replace(/\.gguf$/i,"");
 return /\.gguf$/i.test(filename)&&!stem.toLowerCase().includes("mmproj")&&!/(?:^|[-_.])\d{5}-of-\d{5}$/i.test(stem);
}
function ollamaModelName(repository,path){
 return "hf.co/"+repository+":"+(path||"").split("/").pop();
}
// Mirrors downloads.lemonade_model on the server.
function lemonadeModelName(repository,path){
 const [owner,model]=repository.split("/"),stem=(path||"").split("/").pop().replace(/\.gguf$/i,"");
 const parts=[owner,stem];if(!stem.toLowerCase().startsWith(model.toLowerCase().replace(/-gguf$/,"")))parts.splice(1,0,model);
 const name=parts.join("-").replace(/[^A-Za-z0-9._-]+/g,"-").replace(/^[-._]+|[-._]+$/g,"");
 return "user."+(name.slice(0,150).replace(/[-._]+$/,"")||"model");
}
const LLM_SERVERS={ollama:{label:"Ollama",name:ollamaModelName},lemonade:{label:"Lemonade",name:lemonadeModelName},lmstudio:{label:"LM Studio",name:(repo,path)=>"lmstudio:"+repo+":"+lmstudioQuant(path)}};
function lmstudioQuant(path){const stem=(path||"").split("/").pop().replace(/\.gguf$/i,"");const m=stem.match(/(?:^|[^a-z0-9])(IQ[1-4]_(?:XXS|XS|S|M|NL)|Q[2-8]_[K0-9]+(?:_[SML])?|BF16|F16|F32)(?=$|[^a-z0-9])/i);return m?m[1].toUpperCase():"";}
function fileRow(label,onPc,ref,request,f={},behavior={}){
 // One downloadable file: a destination-specific installed state, active job, or Download button.
 const row=text("div","","browse-file");row.append(text("span",label,"model-sub"));
 // LLM GGUFs are rated against the selected runtime PC when its hardware is known.
 const fitSpan=text("span","","fit");fitSpan.hidden=true;row.append(fitSpan);
 const paintFit=()=>{
  const llm=request.provider==="lemonade"||request.provider==="lmstudio",fit=request.provider==="lmstudio"?f.lmstudio_fit:llm?f.llm_fit:f.fit,best=request.provider==="lmstudio"?f.lmstudio_best:llm?f.llm_best:f.best;
  row.classList.toggle("best",!!best);fitSpan.hidden=!fit;if(!fit)return;
  fitSpan.className="fit fit-"+fit.level;
  fitSpan.textContent=(best?(llm?"★ Best for the "+(request.provider==="lmstudio"?"LM Studio":"Lemonade")+" PC · ":"★ Best for your PC · "):"")+(fit.level==="good"?"✓ ":fit.level==="bad"?"✗ ":"⚠ ")+fit.note;
 };
 const status=text("span","","browse-status");row.append(status);
 const button=text("button","Download","repo-action");button.type="button";row.append(button);
 const currentRef=()=>typeof ref==="function"?ref():ref;
 const installed=()=>typeof onPc==="function"?onPc():onPc;
 const allowed=()=>!behavior.isAllowed||behavior.isAllowed();
 const show=j=>{
  if(!j){status.textContent="";button.hidden=false;return;}
  status.textContent=newDownloadStatus(j);button.hidden=j.state!=="failed";button.textContent=j.state==="failed"?"Retry download":"Download";
  if(j&&j.state==="done"&&j.provider==="lmstudio")refresh();
  if((j.state==="queued"||j.state==="running")&&j.source_ref===currentRef())setTimeout(async()=>{
   if(view!=="browse"||j.source_ref!==currentRef())return;
   try{show((await apiRequest("/api/downloads")).find(x=>x.id===j.id));}catch(_){}
  },3000);
 };
 const render=()=>{
  paintFit();
  const server=LLM_SERVERS[request.provider];
  if(!allowed()){status.textContent=request.provider==="lmstudio"?"LM Studio requires a GGUF with one identifiable quantization in this repository.":(server?server.label:"This destination")+" can import single GGUF model files only.";status.classList.remove("on-pc");button.hidden=true;return;}
  status.classList.remove("on-pc");
  if(installed()){status.textContent=server?"✓ In "+server.label:"✓ On your PC";status.classList.add("on-pc");button.hidden=true;return;}
  button.hidden=false;
  show(browseJobs.find(j=>j.source_ref===currentRef()&&j.state!=="failed")||browseJobs.find(j=>j.source_ref===currentRef()&&j.state==="failed"));
  if(!browseAgent()){button.disabled=true;status.textContent="No PC agent connected";}
  else button.disabled=false;
 };
 if(behavior.destination)behavior.destination.addEventListener("change",render);
 button.addEventListener("click",async()=>{
  if(!allowed())return;
  if(request.provider==="lmstudio"&&!confirm("Download this model through LM Studio?\n\nTarget: "+browseAgent()+" · LM Studio\nRepository: "+request.repository+"\nQuantization: "+lmstudioQuant(request.remote_path)+"\nEstimated download: "+fmt(f.size_bytes||0)+"\n\nLM Studio manages the download; ModelHarbor will not SHA-256 verify it."))return;
  button.disabled=true;
  try{const job=await apiRequest("/api/downloads/new","POST",{agent:browseAgent(),...request});show(job);pollDownloads();$("download-tray").classList.remove("collapsed");}
  catch(err){status.textContent=err.message;button.disabled=false;}
 });
 render();
 return row;
}
function civitaiCard(item){
 if($("browse-compatible").checked&&!item.versions.some(v=>isFamilyCompatible(v.base_model)))return null;
 const card=text("div","","browse-card");
 const mature=$("browse-mature").checked;
 if(item.image&&(item.image.nsfw_level<=1||mature)){const img=document.createElement("img");img.src=item.image.url;img.alt="";img.loading="lazy";img.referrerPolicy="no-referrer";card.append(img);}
 const info=text("div","","browse-info");
 info.append(text("strong",item.name),text("div",[item.type,item.creator,item.downloads!=null?"↓ "+Number(item.downloads).toLocaleString():""].filter(Boolean).join(" · "),"model-sub"));
 const rated=item.versions.flatMap(v=>v.files).filter(f=>f.fit);
  const good=rated.filter(f=>f.fit.level==="good"), best=rated.find(f=>f.best)||good[0];
  const recommendation=text("div","","discovery-reason");
  if(item.installed_version)recommendation.textContent="✓ Already installed — check versions before downloading another copy";
  else if(best){const rec=best.recommendation;recommendation.textContent=(rec&&rec.score!=null?"★ "+rec.score+"/100 · "+rec.label+" · ":"✓ Hardware fit: ")+best.fit.note+" · "+fmt(best.size_bytes);if(rec&&rec.reasons)recommendation.title=rec.reasons.join(" · ");}
  else if(rated.length)recommendation.textContent="⚠ Review compatibility before downloading";
  else recommendation.textContent="ℹ Hardware fit unavailable — inspect versions";
  info.append(recommendation);
  info.append(text("div",item.installed_version?(item.latest_installed?"✓ On your PC ("+item.installed_version+")":"Other version on your PC ("+item.installed_version+")"):"Not on your PC","browse-badge"+(item.installed_version?" on-pc":"")));
 const toggle=text("button","Versions & files","repo-action");toggle.type="button";const files=text("div","","browse-versions");files.hidden=true;
 toggle.setAttribute("aria-expanded","false");toggle.addEventListener("click",()=>{files.hidden=!files.hidden;toggle.setAttribute("aria-expanded",String(!files.hidden));toggle.textContent=files.hidden?"Versions & files":"Hide versions";});
 item.versions.forEach(v=>{files.append(text("div",v.name+(v.base_model?" · "+v.base_model:"")+(v.published_at?" · "+v.published_at.slice(0,10):""),"version-name"));
  v.files.forEach(f=>files.append(fileRow(f.name+" · "+f.type+" · "+fmt(f.size_bytes)+" → "+f.folder,f.on_pc,"civitai:"+f.id,{provider:"civitai",model_id:item.id,version_id:v.id,file_id:f.id},f)));});
 const link=document.createElement("a");link.href=item.url;link.target="_blank";link.rel="noopener noreferrer";link.textContent="Open on Civitai ↗";link.className="model-sub";
 info.append(toggle,link);card.append(info,files);return card;
}
// A Hugging Face or ModelScope repository: both list files with a published SHA-256.
function repoCard(repo){
 const card=text("article","","browse-card repo-discovery-card"),site=providerName(repo.provider);
 const isHF=repo.provider==="huggingface";
 const tags=Array.isArray(repo.tags)?repo.tags:[];
 const task=repo.pipeline_tag||"";
 const isLora=repo.folder==="loras"||tags.includes("lora");
 const title=repo.id.split("/").pop(),author=repo.id.split("/")[0];
 const hero=text("div","","repo-discovery-hero");
 hero.append(text("span",isLora?"◈":/video/i.test(task)?"▣":/image/i.test(task)?"▧":"◇","repo-hero-icon"),
             text("span",isLora?"LoRA":/video/i.test(task)?"Video":/image/i.test(task)?"Image model":"Model repository","repo-hero-type"));
 if(isHF)card.append(hero);
 const info=text("div","","browse-info");
 info.append(text("strong",title,"repo-title"),text("div","by "+author+" · "+site,"model-sub"));
 const chips=text("div","","repo-chips");
 if(task)chips.append(text("span",task.replace(/-/g," "),"repo-chip"));
 if(isLora)chips.append(text("span","LoRA","repo-chip"));
 if(repo.gated)chips.append(text("span","Access approval may be required","repo-chip repo-chip-warning"));
 const format=tags.find(t=>["gguf","safetensors","diffusers","transformers"].includes(t.toLowerCase()));
 if(format)chips.append(text("span",format.toUpperCase(),"repo-chip"));
 if(chips.childElementCount)info.append(chips);
 const metrics=text("div","","repo-metrics");
 if(repo.downloads!=null)metrics.append(text("span","↓ "+Number(repo.downloads).toLocaleString()+" downloads"));
 if(repo.likes!=null)metrics.append(text("span","♥ "+Number(repo.likes).toLocaleString()+" likes"));
 if(repo.updated_at||repo.created_at)metrics.append(text("span","Updated "+String(repo.updated_at||repo.created_at).slice(0,10)));
 info.append(metrics);
 if(isHF)info.append(text("p","Select files to check GPU fit, installed copies, and download options.","repo-explainer"));
 const toggle=text("button","Explore model files","repo-action repo-explore");toggle.type="button";toggle.setAttribute("aria-expanded","false");
 const files=text("div","","browse-versions repo-file-panel");files.hidden=true;
 const folder=document.createElement("select");folder.setAttribute("aria-label","Save into folder");
 ["loras","checkpoints","diffusion_models","text_encoders","vae","embeddings","controlnet","upscale_models"].forEach(f=>folder.append(new Option("Save into "+f,f)));
 if(repo.folder)folder.value=repo.folder;
 let loaded=false;
 toggle.addEventListener("click",async()=>{
  files.hidden=!files.hidden;
  toggle.setAttribute("aria-expanded",String(!files.hidden));
  toggle.textContent=files.hidden?"Explore model files":"Hide model files";
  if(files.hidden||loaded)return;
  files.replaceChildren(text("p","Loading repository files…","muted"));
  try{
   const listing=await apiRequest("/api/"+repo.provider+"/repository?"+new URLSearchParams({repo:repo.id,agent:browseAgent()||""}));
   files.replaceChildren();
   loaded=true;
   if(!listing.files.length){files.append(text("p","No downloadable model files found.","muted"));return;}
   const summary=text("div","","repo-file-summary");
   const installed=listing.files.filter(f=>f.on_pc).length;
   summary.append(text("strong",listing.files.length+" model files · "+installed+" already installed"));
   const input=document.createElement("input");input.type="search";input.placeholder="Filter files or quantizations…";input.setAttribute("aria-label","Filter files in "+repo.id);
   const compatible=document.createElement("label");compatible.className="repo-filter-toggle";
   const check=document.createElement("input");check.type="checkbox";compatible.append(check,document.createTextNode(" Show recommended files only"));
   const results=text("div","","repo-file-results");
   const render=()=>{
    results.replaceChildren();
    const needle=input.value.trim().toLowerCase();
    const visible=listing.files.filter(f=>(!needle||f.path.toLowerCase().includes(needle))&&(!check.checked||(f.recommendation&&f.recommendation.score>=80)||f.best));
    for(const f of visible.slice(0,80)){
     const item=text("div","","repo-file-choice");
     const top=text("div","","repo-file-heading");
     top.append(text("strong",f.name||f.path.split("/").pop()));
     const rec=f.recommendation;
     if(f.on_pc)top.append(text("span","✓ Installed","repo-chip repo-chip-installed"));
     else if(rec&&rec.score!=null)top.append(text("span",rec.score+"/100 · "+rec.label,"repo-chip"));
     else if(f.fit&&f.fit.level)top.append(text("span",f.fit.level==="good"?"✓ Fits GPU":f.fit.level==="ok"?"△ May run":"⚠ Poor fit","repo-chip"));
     item.append(top);
     if(f.path!==(f.name||f.path.split("/").pop()))item.append(text("div",f.path,"model-sub"));
     const note=(rec&&rec.reasons&&rec.reasons.length?rec.reasons.join(" · "):f.fit&&f.fit.note)||"Compatibility not verified";
     item.append(text("div",(f.size_bytes?fmt(f.size_bytes)+" · ":"")+note,"model-sub"));
     item.append(fileRow("",f.on_pc,repo.provider+":"+repo.id+"/"+f.path,
      {provider:repo.provider,repository:repo.id,remote_path:f.path,get folder(){return folder.value;}},f));
     results.append(item);
    }
    if(!visible.length)results.append(text("p","No matching files.","muted"));
    if(visible.length>80)results.append(text("p","Showing first 80 matches; refine the filter to see more.","muted"));
   };
   input.addEventListener("input",render);check.addEventListener("change",render);
   files.append(summary,folder,input,compatible,results);render();
  }catch(err){files.replaceChildren(text("p",err.message,"muted"));}
 });
 const link=document.createElement("a");link.href=repo.url;link.target="_blank";link.rel="noopener noreferrer";link.textContent="Open on "+site+" ↗";link.className="model-sub";
 info.append(toggle,link);card.append(info,files);return card;
}
function hfInspectCard(data){
 const card=text("div","","browse-card inspect-card");const info=text("div","","browse-info");
 const title=text("strong",data.repository);
 const badges=[];
 if(data.base_model)badges.push("Base: "+data.base_model);
 if(data.model_type)badges.push("Type: "+data.model_type);
 if(data.trigger_words&&data.trigger_words.length)badges.push("Trigger: "+data.trigger_words.join(", "));
 if(data.downloads!=null)badges.push("↓ "+Number(data.downloads).toLocaleString());
 if(data.likes)badges.push("♥ "+Number(data.likes).toLocaleString());
 const meta=text("div",badges.join(" · "),"model-sub inspect-meta");
 if(data.trigger_words&&data.trigger_words.length){
  const copyTrig=text("button","📋 Copy trigger words","copy-btn");copyTrig.type="button";
  copyTrig.onclick=()=>copyToClipboard(data.trigger_words.join(", "),copyTrig);meta.append(copyTrig);
 }
 if(data.base_model&&!hasBaseModelFor(data.base_model)){
  const warn=text("div","","companion-warning");
  warn.append(text("span","⚠️ Requires a "+data.base_model+" base model. You don't have one installed yet."));
  const findBtn=text("button","Search "+data.base_model+" checkpoints ↗");findBtn.type="button";
  findBtn.onclick=()=>{
   $("browse-provider").value="civitai";$("browse-type").value="Checkpoint";$("browse-base").value="";$("browse-q").value=data.base_model;browseSearch(false);
  };
  warn.append(findBtn);info.append(warn);
 }
 const folderRow=text("div","","inspect-folder-row");
 folderRow.append(text("span","Save into: ","muted"));
 const folderSelect=document.createElement("select");folderSelect.setAttribute("aria-label","Target folder");
 const hasGguf=data.files.some(f=>isOllamaCompatiblePath(f.path));
 const primaryCanOllama=!!data.primary_file&&isOllamaCompatiblePath(data.primary_file.path)&&
  data.files.filter(f=>f.path.split("/").pop().toLowerCase()===data.primary_file.path.split("/").pop().toLowerCase()).length===1;
 // Each LLM server is offered only while the PC's last sync could reach it.
 const agent=agents.find(a=>a.device===browseAgent());
 const ollamaUp=hasGguf&&(!agent||agent.ollama_count!=null),lemonadeUp=hasGguf&&(!agent||agent.lemonade_count!=null);
 // Default to an LLM server only when the selected file can actually be imported there.
 const llmDefault=primaryCanOllama?(lemonadeUp?"lemonade":ollamaUp?"ollama":null):null;
 (data.available_folders||[data.target_folder,"loras","checkpoints"]).forEach(f=>{
  const opt=new Option(f,f);if(f===data.target_folder&&!llmDefault)opt.selected=true;folderSelect.append(opt);
 });
 if(ollamaUp)folderSelect.prepend(new Option("Ollama (import GGUF)","ollama",llmDefault==="ollama",llmDefault==="ollama"));
 if(lemonadeUp)folderSelect.prepend(new Option("Lemonade (download & load GGUF)","lemonade",llmDefault==="lemonade",llmDefault==="lemonade"));
 const lmstudioFile=hasGguf&&data.files.some(f=>f.path.toLowerCase().endsWith(".gguf")&&lmstudioQuant(f.path));
 if(lmstudioFile&&agent&&agent.online&&agent.lmstudio&&agent.lmstudio.status==="connected"&&agent.lmstudio_count!=null)
  folderSelect.prepend(new Option("LM Studio (download GGUF)","lmstudio"));
 folderRow.append(folderSelect);
 const llm=()=>LLM_SERVERS[folderSelect.value];
 const hfRequest=f=>({repository:data.repository,remote_path:f.path,base_model:data.base_model,trigger_words:data.trigger_words,
  get provider(){return llm()?folderSelect.value:"huggingface";},get folder(){return llm()?null:folderSelect.value;}});
 const hfRef=f=>llm()?(folderSelect.value==="lmstudio"?llm().name(data.repository,f.path):folderSelect.value+":"+llm().name(data.repository,f.path)):"huggingface:"+data.repository+"/"+f.path;
 const hfInstalled=f=>{
  if(!llm())return !!f.on_pc;
  const name=llm().name(data.repository,f.path).toLowerCase(),device=(browseAgent()||"")+":"+folderSelect.value;
  if(folderSelect.value==="lmstudio")return models.some(m=>m.device===device&&m.runtime_meta&&m.runtime_meta.model_key&&m.runtime_meta.model_key.toLowerCase().includes(data.repository.toLowerCase())&&String(m.runtime_meta.quantization&&m.runtime_meta.quantization.name||"").toUpperCase()===lmstudioQuant(f.path));
  return models.some(m=>m.device===device&&m.name.toLowerCase()===name);
 };
 const hfBehavior=f=>({destination:folderSelect,isAllowed:()=>{if(!llm())return true;const unique=data.files.filter(x=>x.path.split("/").pop().toLowerCase()===f.path.split("/").pop().toLowerCase()).length===1;return folderSelect.value==="lmstudio"?f.path.toLowerCase().endsWith(".gguf")&&!!lmstudioQuant(f.path)&&data.files.filter(x=>x.path.toLowerCase().endsWith(".gguf")&&lmstudioQuant(x.path)===lmstudioQuant(f.path)).length===1:isOllamaCompatiblePath(f.path)&&unique;}});
 const primaryBox=text("div","","primary-file-box");
 if(data.primary_file){
  const pf=data.primary_file;
  const header=text("div","","primary-header");
  header.append(text("span",data.specified_path?"★ Selected file from link":"★ Recommended file for ComfyUI","primary-badge"));
  primaryBox.append(header);
  primaryBox.append(fileRow(pf.name+(pf.size_bytes?" · "+fmt(pf.size_bytes):"")+(pf.sha256?"":" · no published hash"),
   ()=>hfInstalled(pf),()=>hfRef(pf),
   hfRequest(pf),pf,hfBehavior(pf)));
 }else{
  primaryBox.append(text("p","No model files found in repository.","muted"));
 }
 const toggle=text("button","Browse all "+data.files.length+" repository files ▾","repo-action");toggle.type="button";
 const files=text("div","","browse-versions");files.hidden=true;
 toggle.addEventListener("click",()=>{
  files.hidden=!files.hidden;
  toggle.textContent=files.hidden?"Browse all "+data.files.length+" repository files ▾":"Hide repository files ▴";
 });
 data.files.forEach(f=>{
  const isPrimary=data.primary_file&&f.path===data.primary_file.path;
  files.append(fileRow(f.path+(isPrimary?" (recommended)":"")+(f.size_bytes?" · "+fmt(f.size_bytes):"")+(f.sha256?"":" · no published hash"),
   ()=>hfInstalled(f),()=>hfRef(f),
   hfRequest(f),f,hfBehavior(f)));
 });
 const link=document.createElement("a");link.href=data.url;link.target="_blank";link.rel="noopener noreferrer";link.textContent="Open on Hugging Face ↗";link.className="model-sub";
 info.append(title,meta,folderRow,primaryBox,toggle,link);card.append(info,files);return card;
}
async function inspectLink(url){
 url=(url||"").trim();if(!url)return;
 const list=$("browse-list");list.replaceChildren(text("p","Inspecting Hugging Face repository…","muted"));
 $("browse-results").textContent="Inspecting link…";$("browse-more").hidden=true;
 try{
  browseJobs=await apiRequest("/api/downloads").catch(()=>[]);
  const data=await apiRequest("/api/huggingface/inspect?"+new URLSearchParams({url,agent:browseAgent()||""}));
  list.replaceChildren(hfInspectCard(data));
  $("browse-results").textContent="1 repository ("+(data.base_model?data.base_model+" ":"")+data.model_type+")";
 }catch(err){
  list.replaceChildren(text("p",err.message||"Failed to inspect repository","muted"));$("browse-results").textContent="";
 }
}
async function browseSearch(more){
 const list=$("browse-list"),provider=$("browse-provider").value,q=$("browse-q").value.trim();
 if(!more&&(q.startsWith("http://")||q.startsWith("https://")||q.includes("huggingface.co")||q.includes("hf.co")||(provider==="huggingface"&&/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(q)))){
  return inspectLink(q);
 }
 if(!more){list.replaceChildren(text("p","Searching…","muted"));browseCursor=null;}
 $("browse-go").disabled=$("browse-more").disabled=true;
 try{
  browseJobs=await apiRequest("/api/downloads").catch(()=>[]);
  if(provider==="huggingface"){
   const data=await apiRequest("/api/browse/huggingface?"+new URLSearchParams({q,type:$("browse-hf-type").value,sort:$("browse-hf-sort").value}));
   list.replaceChildren(...data.items.map(repoCard));$("browse-more").hidden=true;
   $("browse-results").textContent=data.items.length+" repositories";
  }else if(provider==="modelscope"){
   const params=new URLSearchParams({q,type:$("browse-ms-type").value,sort:$("browse-ms-sort").value});
   if(more&&browseCursor)params.set("cursor",browseCursor);
   const data=await apiRequest("/api/browse/modelscope?"+params);
   if(!more)list.replaceChildren();
   data.items.forEach(item=>list.append(repoCard(item)));
   browseCursor=data.next_cursor;$("browse-more").hidden=!browseCursor;
   $("browse-results").textContent=list.querySelectorAll(".browse-card").length+" repositories";
  }else{
   const params=new URLSearchParams({q,type:$("browse-type").value,base:$("browse-base").value,sort:$("browse-sort").value,agent:browseAgent()||""});
   if(more&&browseCursor)params.set("cursor",browseCursor);
   const data=await apiRequest("/api/browse/civitai?"+params);
   if(!more)list.replaceChildren();
   data.items.forEach(item=>{const c=civitaiCard(item);if(c)list.append(c);});
   browseCursor=data.next_cursor;$("browse-more").hidden=!browseCursor;
   const hw=(agents.find(a=>a.device===browseAgent())||{}).hardware;
   $("browse-results").textContent=list.querySelectorAll(".browse-card").length+" models"+(hw&&hw.gpu?" · rated for "+hw.gpu:"");
  }
  if(!list.childElementCount)list.append(text("p","Nothing found. Try fewer filters.","muted"));
 }catch(err){if(!more)list.replaceChildren();list.append(text("p",err.message,"muted"));}
 finally{$("browse-go").disabled=$("browse-more").disabled=false;}
}
$("browse-link-btn").addEventListener("click",()=>inspectLink($("browse-link").value));
$("browse-link").addEventListener("keydown",e=>{if(e.key==="Enter")inspectLink($("browse-link").value);});
$("browse-go").addEventListener("click",()=>browseSearch(false));
$("browse-more").addEventListener("click",()=>browseSearch(true));
$("browse-q").addEventListener("keydown",e=>{if(e.key==="Enter")browseSearch(false);});
$("browse-provider").addEventListener("change",()=>{const site=$("browse-provider").value;["browse-type","browse-base","browse-sort"].forEach(id=>$(id).hidden=site!=="civitai");["browse-hf-type","browse-hf-sort"].forEach(id=>$(id).hidden=site!=="huggingface");["browse-ms-type","browse-ms-sort"].forEach(id=>$(id).hidden=site!=="modelscope");});
try{$("browse-mature").checked=localStorage.getItem("modelharbor_mature")==="1";}catch(_){}
$("browse-mature").addEventListener("change",()=>{try{localStorage.setItem("modelharbor_mature",$("browse-mature").checked?"1":"0");}catch(_){}});
try{$("browse-compatible").checked=localStorage.getItem("modelharbor_browse_compatible")==="1";}catch(_){}
$("browse-compatible").addEventListener("change",()=>{try{localStorage.setItem("modelharbor_browse_compatible",$("browse-compatible").checked?"1":"0");}catch(_){}browseSearch(false);});
function render(){
 $("count").textContent=models.length.toLocaleString();
 $("size").textContent=fmt(models.reduce((n,m)=>n+(m.hard_link?0:Number(m.size_bytes)||0),0));
 const devices=[...new Set(models.map(m=>m.device))].sort();
 const physical=[...new Set(models.map(m=>m.physical_device||m.device))];
 $("devices-count").textContent=physical.length;
 const files=models.filter(m=>!m.hard_link);
 $("updates-count").textContent=files.filter(m=>m.update).length.toLocaleString();
 $("identified-count").textContent=files.filter(m=>m.source).length+" of "+files.length+" files identified";
 const toMatch=files.filter(isUnmatched).length;$("unmatched-link").hidden=!toMatch;$("unmatched-link").textContent=toMatch+" to match →";
 fill($("recent"),models.slice(0,8),"No models indexed yet. Run the inventory agent on your PC.");
 const counts=new Map();models.forEach(m=>counts.set(m.kind,(counts.get(m.kind)||0)+1));
 $("types").replaceChildren();[...counts].sort((a,b)=>b[1]-a[1]).slice(0,10).forEach(([kind,count])=>{const row=text("div");const line=text("div","","type-line");line.append(text("span",kind),text("span",String(count)));const bar=text("div","","bar");const inner=text("span");inner.style.width=(count/Math.max(models.length,1)*100)+"%";bar.append(inner);row.append(line,bar);$("types").append(row);});
 if(!counts.size)$("types").append(text("p","Model categories will appear after the first inventory scan.","muted"));
 options("kind",[...counts.keys()].sort(),"All types");options("device",devices,"All sources");options("family",[...new Set(models.map(m=>m.family_guess||"Unknown"))].sort(),"All families");options("folder",[...new Set(models.filter(m=>m.kind==="lora").map(m=>m.path.replace(/[\\/][^\\/]+$/,"")))].sort(),"All LoRA folders");
 const deviceList=$("device-list");deviceList.replaceChildren();
 renderAgents();fillBrowseAgents();
 devices.forEach(device=>{const matching=models.filter(m=>m.device===device);const b=text("button","","model");b.append(text("span","▣","glyph"));const info=text("span","","model-text");info.append(text("div",device,"model-name"),text("div",matching.length+" models · "+fmt(matching.reduce((n,m)=>n+(m.hard_link?0:m.size_bytes),0)),"model-sub"));b.append(info,text("span","›","chevron"));b.onclick=()=>{switchView("library");$("device").value=device;filter();};deviceList.append(b);});
 if(!devices.length)deviceList.append(text("p","No inventory sources registered.","muted"));
 filter();
}
function filter(){
 const q=$("search").value.trim().toLowerCase(),kind=$("kind").value,device=$("device").value;
 const family=$("family").value, folder=$("folder").value, duplicates=$("duplicates").checked, updates=$("updates").checked, unmatched=$("unmatched").checked, sort=$("sort").value;
 const subset=models.filter(m=>(!q||(m.name+" "+m.path).toLowerCase().includes(q))&&(!kind||m.kind===kind)&&(!device||m.device===device)&&(!family||m.family_guess===family)&&(!folder||m.kind==="lora"&&m.path.replace(/[\\/][^\\/]+$/,"")===folder)&&(!duplicates||m.possible_duplicate)&&(!updates||m.update)&&(!unmatched||isUnmatched(m)));
 subset.sort((a,b)=>sort==="size-desc"?b.size_bytes-a.size_bytes:sort==="size-asc"?a.size_bytes-b.size_bytes:sort==="kind"?a.kind.localeCompare(b.kind)||a.name.localeCompare(b.name):a.name.localeCompare(b.name));
 $("results").textContent=subset.length+" results";
 const active=["kind","device","family","folder"].filter(id=>$(id).value).length+["duplicates","updates","unmatched"].filter(id=>$(id).checked).length;
 $("filter-toggle").textContent="Filters & sort"+(active?" ("+active+" active)":"");
 $("auto-match-btn").hidden=!unmatched||!models.some(isUnmatched);
 fill($("library"),subset.slice(0,500),"No matching models.");
 if(subset.length>500)$("library").append(text("p","Showing first 500 results. Narrow your filters to see more.","muted"));
}
async function refresh(){if(!token){openAuth();return;} $("refresh").disabled=true;try{const response=await fetch("/api/models",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});if(response.status===401){token="";saveToken("");setStatus(false);showNotice("Token rejected. Enter your configured API token.");openAuth();return;}if(!response.ok)throw Error("Server returned HTTP "+response.status);const data=await response.json();if(!Array.isArray(data))throw Error("Unexpected inventory response");models=data;try{selectedMatches=await apiRequest("/api/selected-matches");}catch(_){selectedMatches=[];}try{const agentsResponse=await fetch("/api/agents",{headers:{"X-ModelHarbor-Token":token},cache:"no-store"});agents=agentsResponse.ok?await agentsResponse.json():[];}catch(_){agents=[];}setStatus(true);showNotice("");render();pollDownloads();loadConverts();}catch(err){setStatus(false);showNotice("Unable to load inventory: "+err.message);}finally{$("refresh").disabled=false;}}
document.querySelectorAll(".tab").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.view)));
window.addEventListener("hashchange",()=>{const v=location.hash.slice(1)||"overview";if(VIEWS.includes(v)&&v!==view)switchView(v);});
if(VIEWS.includes(location.hash.slice(1)))switchView(location.hash.slice(1));
$("filter-toggle").addEventListener("click",()=>{const open=$("library-filters").classList.toggle("open");$("filter-toggle").setAttribute("aria-expanded",open);});
document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>switchView(b.dataset.go)));
["search","kind","device","family","folder","sort","duplicates","updates","unmatched"].forEach(id=>$(id).addEventListener(id==="search"?"input":"change",filter));
$("unmatched-link").addEventListener("click",()=>{switchView("library");$("unmatched").checked=true;$("updates").checked=$("duplicates").checked=false;filter();});
$("refresh").onclick=refresh;$("settings").onclick=openAuth;
$("auth-form").addEventListener("submit",e=>{e.preventDefault();token=$("token").value.trim();saveToken(token,$("remember").checked);$("auth-dialog").close();refresh();});
$("cancel-auth").onclick=()=>$("auth-dialog").close();
$("close-detail").onclick=$("detail-done").onclick=()=>$("detail-dialog").close();
$("close-automatch").onclick=$("automatch-done").onclick=()=>{ $("automatch-dialog").close(); refresh(); };
$("auto-match-btn").addEventListener("click",openAutoMatchWizard);
async function openAutoMatchWizard(){
 const unmatchedModels=models.filter(isUnmatched);
 if(!unmatchedModels.length){showNotice("All models are already matched or marked local only!");return;}
 $("automatch-dialog").showModal();
 const status=$("automatch-status"),content=$("automatch-content");
 content.replaceChildren();
 status.textContent="Scanning "+unmatchedModels.length+" unmatched model"+(unmatchedModels.length===1?"":"s")+" against Civitai & Hugging Face…";
 const matched=[],noMatch=[];
 for(let i=0;i<unmatchedModels.length;i++){
  const m=unmatchedModels[i];
  if(!$("automatch-dialog").open)return;  // closed: stop querying Civitai and Hugging Face
  status.textContent="Checking "+(i+1)+" of "+unmatchedModels.length+": "+m.name+"…";
  try{
   const res=await apiRequest("/api/models/suggest-match?"+new URLSearchParams({device:m.device,path:m.path}));
   const cv=res.civitai||[],hf=res.huggingface||[];
   if(cv.length||hf.length){matched.push({model:m,civitai:cv,huggingface:hf});}
   else{noMatch.push(m);}
  }catch(_){noMatch.push(m);}
 }
 status.textContent="Scan complete: "+matched.length+" match candidate"+(matched.length===1?"":"s")+" found · "+noMatch.length+" local utility/untracked file"+(noMatch.length===1?"":"s")+".";
 renderAutoMatchContent(matched,noMatch);
}
function renderAutoMatchContent(matched,noMatch){
 const content=$("automatch-content");content.replaceChildren();
 if(matched.length){
  const sec=text("div","","automatch-section"),bar=text("div","","automatch-actions-bar");
  bar.append(text("strong","Found online match candidates ("+matched.length+")"));
  const hasExact=matched.some(item=>(item.civitai&&item.civitai.some(c=>c.verdict==="exact"||c.match_type==="exact"))||(item.huggingface&&item.huggingface.some(h=>h.verdict==="exact"||h.match_type==="exact")));
  if(hasExact){
   const linkAllBtn=text("button","⚡ Link all exact matches","primary");linkAllBtn.type="button";
   linkAllBtn.onclick=async()=>{
    linkAllBtn.disabled=true;
    for(const item of matched){
     const m=item.model;
     const exactCv=item.civitai&&item.civitai.find(c=>c.verdict==="exact"||c.match_type==="exact");
     const exactHf=item.huggingface&&item.huggingface.find(h=>h.verdict==="exact"||h.match_type==="exact");
     try{
      if(exactCv){await apiRequest("/api/civitai/link","POST",{device:m.device,path:m.path,model_id:exactCv.model_id,version_id:exactCv.version_id,file_id:exactCv.file_id});}
      else if(exactHf){await apiRequest("/api/huggingface/link","POST",{device:m.device,path:m.path,repository:exactHf.repository,remote_path:exactHf.remote_path,sha256:exactHf.sha256,base_model:exactHf.base_model,trigger_words:exactHf.trigger_words});}
     }catch(_){}
    }
    await refresh();openAutoMatchWizard();
   };
   bar.append(linkAllBtn);
  }
  sec.append(bar);
  matched.forEach(item=>{
   const m=item.model,row=text("div","","automatch-card"),left=text("div","","automatch-left");
   left.append(text("strong",m.name),text("small",m.kind+" · "+fmt(m.size_bytes)+" · "+m.path,"model-sub"));
   const topCandidate=(item.civitai&&item.civitai[0])||(item.huggingface&&item.huggingface[0]);
   if(topCandidate){
    const prov=topCandidate.provider==="civitai"?"Civitai":"Hugging Face";
    const candTitle=topCandidate.model_name||topCandidate.name||topCandidate.repository;
    const vName=topCandidate.version_name?" ("+topCandidate.version_name+")":topCandidate.remote_path?" / "+topCandidate.remote_path:"";
    const badge=verdictLabel(topCandidate);
    left.append(text("span","Candidate: "+prov+" · "+candTitle+vName+" ("+badge+")","match-possible"));
   }
   const actions=text("div","","suggest-match-actions"),linkBtn=text("button","Link","primary");linkBtn.type="button";
   linkBtn.onclick=async()=>{
    if(!confirmLink(topCandidate,m))return;
    linkBtn.disabled=true;
    try{
     if(topCandidate.provider==="civitai"){
      await apiRequest("/api/civitai/link","POST",{device:m.device,path:m.path,model_id:topCandidate.model_id,version_id:topCandidate.version_id,file_id:topCandidate.file_id});
     }else{
      await apiRequest("/api/huggingface/link","POST",{device:m.device,path:m.path,repository:topCandidate.repository,remote_path:topCandidate.remote_path,sha256:topCandidate.sha256,base_model:topCandidate.base_model,trigger_words:topCandidate.trigger_words});
     }
     linkBtn.textContent="✓ Linked";linkBtn.classList.remove("primary");linkBtn.classList.add("repo-action");
    }catch(err){linkBtn.textContent="Error: "+err.message;linkBtn.disabled=false;}
   };
   actions.append(linkBtn);row.append(left,actions);sec.append(row);
  });
  content.append(sec);
 }
 if(noMatch.length){
  const sec=text("div","","automatch-section"),bar=text("div","","automatch-actions-bar");
  bar.append(text("strong","Local utilities or untracked models ("+noMatch.length+")"));
  const markAllBtn=text("button","Mark all "+noMatch.length+" as local only","repo-action");markAllBtn.type="button";
  markAllBtn.onclick=async()=>{
   markAllBtn.disabled=true;
   for(const m of noMatch){
    try{await apiRequest("/api/match-ignore","POST",{device:m.device,path:m.path,ignored:true});}catch(_){}
   }
   await refresh();openAutoMatchWizard();
  };
  bar.append(markAllBtn);sec.append(bar);
  noMatch.forEach(m=>{
   const row=text("div","","automatch-nomatch-row"),info=text("div","","suggest-match-info");
   info.append(text("strong",m.name),text("small",m.kind+" · "+m.path,"model-sub"));
   const btn=text("button","Mark local only","repo-action");btn.type="button";
   btn.onclick=async()=>{
    btn.disabled=true;
    try{await apiRequest("/api/match-ignore","POST",{device:m.device,path:m.path,ignored:true});btn.textContent="✓ Local only";}
    catch(_){btn.disabled=false;}
   };
   row.append(info,btn);sec.append(row);
  });
  content.append(sec);
 }
}
let converts=[],convertsLoaded=false;
// Names that already say 8-bit or smaller (keep in step with fit.COMPACT).
const COMPACT_NAME=/(?<![a-z0-9])(fp8\w*|e4m3fn|e5m2|scaled|int[48]\w*|convrot|nf4|fp4|nvfp4|mxfp[48]|w4a\d+|w8a8|q[2-8](?:_k)?(?:_[sml])?|q8_0|q[45]_[01])(?![a-z0-9])/i;
const GiB=1024**3;
function vramFor(device){const a=agents.find(x=>x.device===device.replace(/:comfyui$/,""));return (a&&a.hardware&&a.hardware.vram_bytes)||0;}
function convertCandidates(){
 const started=new Set(converts.filter(j=>j.state!=="failed").map(j=>j.sha256));
 // Same 2 GB headroom as the download fit check: ComfyUI also needs room for activations.
 return models.filter(m=>m.device.endsWith(":comfyui")&&(m.kind==="diffusion"||m.kind==="text_encoder")&&/\.safetensors$/i.test(m.name)
  &&m.sha256&&!m.hard_link&&!COMPACT_NAME.test(m.name)&&!started.has(m.sha256)&&vramFor(m.device)&&m.size_bytes>vramFor(m.device)-2*GiB)
  .sort((a,b)=>b.size_bytes-a.size_bytes);
}
async function loadConverts(){
 const before=converts.filter(j=>j.state==="done").length,first=!convertsLoaded;
 try{converts=await apiRequest("/api/converts");}catch(_){return;}
 convertsLoaded=true;renderConvert();
 // The PC rescans and hashes the new file before it shows in the library.
 if(!first&&converts.filter(j=>j.state==="done").length>before)setTimeout(refresh,60000);
}
function renderConvert(){
 const hw=agents.filter(a=>a.hardware&&a.hardware.vram_bytes).map(a=>a.device+": "+(a.hardware.gpu||"GPU")+" · "+(a.hardware.vram_bytes/GiB).toFixed(0)+" GB VRAM");
 $("convert-hw").textContent=hw.join(" · ")||"Waiting for the PC agent to report its GPU.";
 const list=$("convert-list");list.replaceChildren();
 const items=convertCandidates();
 if(!items.length)list.append(text("p",hw.length?"Every 16- and 32-bit diffusion model and text encoder fits in your VRAM.":"","muted"));
 items.forEach(m=>{
  const vram=vramFor(m.device),after=m.size_bytes/2,row=text("div","","model convert-row"),info=text("span","","model-text");
  info.append(text("div",m.name,"model-name"),text("div",m.kind+" · "+fmt(m.size_bytes)+" → about "+fmt(after)+" · "+(after<=vram-2*GiB?"fits in your VRAM after converting":"still bigger than your VRAM, but less to offload"),"model-sub"));
  const b=text("button","Convert","primary");b.type="button";
  b.addEventListener("click",async()=>{
   if(!confirm("Convert "+m.name+" to int8_convrot on "+m.device.replace(/:comfyui$/,"")+"?\n\nA new file of about "+fmt(after)+" is written next to it; the original is not changed. It takes a few minutes and runs at low priority."))return;
   b.disabled=true;
   try{await apiRequest("/api/converts","POST",{device:m.device,path:m.path});await loadConverts();}catch(err){showNotice("Conversion not started: "+err.message);b.disabled=false;}
  });
  row.append(text("span","⇣","glyph"),info,b);list.append(row);
 });
 const jobs=$("convert-jobs");jobs.replaceChildren();
 if(!converts.length)jobs.append(text("p","No conversions yet.","muted"));
 converts.forEach(j=>{
  const row=text("div","","model convert-row"),info=text("span","","model-text"),name=j.path.split(/[\\/]/).pop();
  const state=j.state==="queued"?"Waiting for the PC's background task (up to 20 seconds)…":j.state==="running"?"Converting… ComfyUI stays usable meanwhile":
   j.state==="failed"?"✗ "+(j.error||"failed"):"✓ Saved "+j.result_path.split(/[\\/]/).pop()+" ("+fmt(j.result_size)+")";
  info.append(text("div",name,"model-name"),text("div",state,"model-sub"));row.append(text("span",j.state==="done"?"✓":"⇣","glyph"),info);
  const original=j.state==="done"&&models.find(m=>m.device===j.device&&m.path===j.path&&m.sha256===j.sha256);
  if(original){
   const b=text("button","Delete original (frees "+fmt(original.size_bytes)+")","repo-action danger");b.type="button";
   b.addEventListener("click",async()=>{
    if(!confirm("Permanently delete the original "+name+"?\n\nOnly do this after trying the converted copy in ComfyUI. The PC re-checks the file's SHA-256 first. This cannot be undone."))return;
    b.disabled=true;
    try{
     let del=await apiRequest("/api/models/delete","POST",{device:original.device,path:original.path});
     while(del.state==="queued"||del.state==="running"){b.textContent="Deleting…";await new Promise(r=>setTimeout(r,3000));del=await apiRequest("/api/deletes/"+encodeURIComponent(del.id));}
     if(del.state==="failed")throw Error(del.error);
     await refresh();showNotice("Deleted the original "+name);
    }catch(err){showNotice("Not deleted: "+err.message);b.disabled=false;b.textContent="Delete original";}
   });
   row.append(b);
  }
  jobs.append(row);
 });
}
let allDownloads=[];
function renderDownloadTray(){
 const tray=$("download-tray");if(!tray)return;
 const active=allDownloads.filter(j=>j.state==="queued"||j.state==="running");
 if(!allDownloads.length){tray.hidden=true;return;}
 tray.hidden=false;
 $("tray-count").textContent=active.length?active.length+" active":allDownloads.length+" total";
 const items=$("tray-items");items.replaceChildren();
 allDownloads.slice(0,15).forEach(j=>{
  const row=text("div","","tray-item"),top=text("div","","tray-item-top");
  const label=j.file_name||j.source_ref||"Download";
  top.append(text("span",label,"tray-item-name"),text("span",j.state,"tray-item-status"));
  row.append(top);
  if(j.state==="running"&&j.size_bytes){
   const bar=text("div","","tray-item-progress"),fill=text("span");
   fill.style.width=Math.min(100,(j.bytes_done/j.size_bytes)*100)+"%";bar.append(fill);row.append(bar);
   row.append(text("small",fmt(j.bytes_done)+" / "+fmt(j.size_bytes),"model-sub"));
  }else if(j.state==="done"){
   row.append(text("small",j.provider==="lmstudio"?"✓ Downloaded by LM Studio (not hash verified)":j.provider.startsWith("lmstudio-")?"✓ LM Studio "+j.provider.slice("lmstudio-".length)+" request finished":"✓ Verified & saved: "+shortPath(j.result_path),"model-sub"));
  }else if(j.state==="failed"){
   row.append(text("small","✗ "+(j.error||"failed"),"model-sub"));
  }
  items.append(row);
 });
}
async function pollDownloads(){
 if(!token)return;
 try{allDownloads=await apiRequest("/api/downloads");renderDownloadTray();}catch(_){}
}
$("tray-header").onclick=()=>$("download-tray").classList.toggle("collapsed");
setInterval(()=>{if(token&&!document.hidden){pollDownloads();if(converts.some(j=>j.state==="queued"||j.state==="running"))loadConverts();}},4000);
if(token)refresh();else openAuth();
if("serviceWorker" in navigator)navigator.serviceWorker.register("/sw.js").catch(()=>{});
})();