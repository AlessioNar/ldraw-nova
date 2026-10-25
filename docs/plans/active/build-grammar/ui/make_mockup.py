"""Writes index.html: a static, clickable mockup of the building form (component tree + form + preview + presets).

    python docs/plans/active/build-grammar/ui/make_mockup.py

The form fields are generated from ldraw_tools.grammar.SCHEMA, so the mockup shows exactly the parameters the engine has.
The preview shows the rendered preset images from ../renders; in the app it would be the viewer fed by the regenerated plan.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from ldraw_tools import grammar  # noqa: E402
from ldraw_tools.generators.colours import NAMES  # noqa: E402

HERE = Path(__file__).parent
THEME = (ROOT / 'app/web/frontend/src/styles/theme.css').read_text(encoding='utf-8')
THEME = THEME[THEME.index(':root {'):]          # tokens only, no @font-face (the mockup uses the system fallbacks)

PAGE = r'''<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Building grammar - form mockup</title>
<style>
__THEME__
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--fg);font:var(--fs-md)/1.45 var(--font-text)}
.mono{font-family:var(--font-mono)} .label{font-size:var(--fs-label);letter-spacing:var(--track-label);text-transform:uppercase;color:var(--muted)}
header{display:flex;gap:var(--sp-3);align-items:center;padding:var(--sp-3) var(--sp-4);border-bottom:1px solid var(--border);background:var(--surface)}
header h1{font-size:var(--fs-lg);margin:0} .grow{flex:1}
.tabs{display:flex;gap:var(--sp-1)} .tab{padding:6px 12px;border:1px solid var(--border);border-radius:var(--radius-pill);background:none;color:var(--fg-2);cursor:pointer;font:inherit}
.tab[aria-selected=true]{background:var(--accent-soft);border-color:var(--accent);color:var(--fg)}
button.btn{font:inherit;border:1px solid var(--border-strong);background:var(--surface-2);color:var(--fg);border-radius:var(--radius);padding:6px 12px;cursor:pointer}
button.primary{background:var(--accent-solid);border-color:var(--accent-edge);color:var(--accent-fg);box-shadow:0 2px 0 var(--accent-edge)}
.presets{display:flex;gap:var(--sp-2);align-items:center;padding:var(--sp-2) var(--sp-4);border-bottom:1px solid var(--border);overflow:auto}
.chip{padding:4px 10px;border:1px solid var(--border);border-radius:var(--radius-pill);background:var(--surface);color:var(--fg-2);cursor:pointer;white-space:nowrap;font:inherit;font-size:var(--fs-sm)}
.chip[aria-pressed=true]{border-color:var(--accent);color:var(--fg);background:var(--accent-soft)}
main{display:grid;grid-template-columns:230px minmax(320px,1fr) minmax(300px,1.1fr);gap:0;min-height:calc(100vh - 110px)}
@media(max-width:900px){main{grid-template-columns:1fr}}
nav{border-right:1px solid var(--border);padding:var(--sp-3);background:var(--surface)}
.tree,.tree ul{list-style:none;margin:0;padding-left:var(--sp-3)} .tree{padding:0}
.node{display:flex;align-items:center;gap:6px;width:100%;text-align:left;padding:6px 8px;border:0;border-radius:var(--radius-sm);background:none;color:var(--fg-2);font:inherit;cursor:pointer}
.node:hover{background:var(--surface-2)} .node[aria-current=true]{background:var(--surface-3);color:var(--fg);box-shadow:inset 2px 0 var(--accent)}
.node .dot{width:9px;height:9px;border-radius:2px;background:var(--border-strong)} .node small{margin-left:auto;color:var(--muted)}
section.form{padding:var(--sp-4) var(--sp-5);border-right:1px solid var(--border);overflow:auto}
section.form h2{margin:0 0 var(--sp-1);font-size:var(--fs-lg)} .about{color:var(--muted);margin:0 0 var(--sp-4);font-size:var(--fs-sm)}
.field{display:grid;grid-template-columns:150px 1fr;gap:var(--sp-2) var(--sp-3);align-items:center;margin-bottom:var(--sp-3)}
.field label{color:var(--fg-2);font-size:var(--fs-sm)} .field .hint{grid-column:2;color:var(--muted);font-size:var(--fs-xs);margin-top:-4px}
select,input[type=number],textarea{font:inherit;background:var(--surface-2);color:var(--fg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:5px 8px;width:100%}
.range{display:flex;gap:var(--sp-2);align-items:center} .range input[type=range]{flex:1;accent-color:var(--accent-solid)} .range input[type=number]{width:70px;font-family:var(--font-mono)}
.sw{display:flex;gap:8px;align-items:center} .sw i{width:20px;height:20px;border-radius:5px;border:1px solid var(--border-strong);flex:none}
.seg{display:flex;flex-wrap:wrap;gap:4px} .seg button{font:inherit;font-size:var(--fs-sm);padding:4px 9px;border:1px solid var(--border);border-radius:var(--radius-sm);background:var(--surface);color:var(--fg-2);cursor:pointer}
.seg button[aria-pressed=true]{background:var(--info-soft);border-color:var(--info-border);color:var(--fg)}
.switch{width:38px;height:22px;border-radius:99px;background:var(--surface-3);border:1px solid var(--border-strong);position:relative;cursor:pointer;padding:0}
.switch::after{content:"";position:absolute;top:2px;left:2px;width:16px;height:16px;border-radius:50%;background:var(--fg-2);transition:transform var(--t) var(--ease)}
.switch[aria-checked=true]{background:var(--ok-soft);border-color:var(--ok)} .switch[aria-checked=true]::after{transform:translateX(16px);background:var(--ok)}
aside{padding:var(--sp-4);background:var(--canvas);background-image:linear-gradient(var(--grid-minor-c) 1px,transparent 1px),linear-gradient(90deg,var(--grid-minor-c) 1px,transparent 1px);background-size:var(--grid-minor) var(--grid-minor);display:flex;flex-direction:column;gap:var(--sp-3)}
.view{position:relative;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--surface);overflow:hidden;box-shadow:var(--shadow)}
.view img{display:block;width:100%;height:auto} .view .tag{position:absolute;top:8px;left:8px}
.badge{display:inline-block;font-size:var(--fs-xs);padding:2px 8px;border-radius:99px;border:1px solid var(--border);background:var(--surface-2);color:var(--fg-2)}
.badge.ok{background:var(--ok-soft);border-color:var(--ok);color:var(--ok)} .badge.warn{background:var(--warn-soft);border-color:var(--warn);color:var(--warn)}
.notes{display:flex;flex-direction:column;gap:6px} .note{padding:8px 10px;border-radius:var(--radius);background:var(--warn-soft);border:1px solid var(--warn);font-size:var(--fs-sm)}
pre{margin:0;padding:10px;border-radius:var(--radius);background:var(--surface);border:1px solid var(--border);font:var(--fs-xs)/1.5 var(--font-mono);overflow:auto;max-height:200px}
.row{display:flex;gap:var(--sp-2);align-items:center;flex-wrap:wrap}
</style></head><body>
<header><h1>Building</h1><span class="badge mono">generator: building</span><span class="badge ok">no AI</span><span class="grow"></span>
<div class="tabs" role="tablist" aria-label="Workspace tabs"><button class="tab" role="tab" aria-selected="false">Chat</button><button class="tab" role="tab" aria-selected="true">Settings</button><button class="tab" role="tab" aria-selected="false">Parts</button></div>
<button class="btn" id="theme">Light / dark</button></header>
<div class="presets" role="group" aria-label="Presets"><span class="label">Presets</span><span id="presetChips" class="row"></span><span class="grow"></span>
<button class="btn" id="savePreset">Save as preset…</button></div>
<main>
<nav aria-label="Components"><div class="label" style="margin-bottom:8px">Component tree</div><ul class="tree" id="tree"></ul>
<p class="about" style="margin-top:16px">Each node is one component of the grammar. Click a node to edit it. A dot turns red when a rule changed one of its values.</p></nav>
<section class="form" id="form" aria-live="polite"></section>
<aside><div class="row"><span class="label">Live preview</span><span class="badge" id="status">up to date</span><span class="grow"></span>
<button class="btn" id="viewFront" aria-pressed="false">Front</button><button class="btn" id="view34" aria-pressed="true">3/4</button></div>
<div class="view"><span class="badge tag mono" id="previewTag">castle-keep</span><img id="preview" alt="Preview of the building"></div>
<div class="row"><span class="badge mono" id="stats">~1301 parts</span><span class="badge ok">1 connected piece</span><span class="badge">validated: no geometry errors</span></div>
<div class="notes" id="notes"></div>
<div><div class="label" style="margin-bottom:4px">Describe a change (no model call)</div>
<div class="row"><input id="nl" style="flex:1;font:inherit;background:var(--surface-2);color:var(--fg);border:1px solid var(--border);border-radius:var(--radius-sm);padding:6px 8px" placeholder="add a tower at each corner, arched windows, three storeys"><button class="btn" id="nlGo">Apply</button></div>
<div class="about" id="nlOut" style="margin-top:6px"></div></div>
<details><summary class="label" style="cursor:pointer">Configuration (what Regenerate sends)</summary><pre id="json"></pre></details>
<div class="row"><button class="btn primary" id="regen">Regenerate</button><span class="about" style="margin:0">One undo step. Replaces manual edits, like today's Settings tab.</span></div></aside>
</main>
<script>
const SCHEMA = __SCHEMA__; const PRESETS = __PRESETS__; const COLOURS = __COLOURS__; const RULES = __RULES__;
const ORDER = ['footprint','storeys','walls','openings','corners','roof','details','base'];
const TITLE = {footprint:'Footprint',storeys:'Storeys',walls:'Walls',openings:'Openings',corners:'Corners',roof:'Roof',details:'Details',base:'Base'};
const CHILD = {openings:['Doors','Windows'],corners:['Pillars','Towers'],roof:['Slopes','Battlements'],details:['Chimney','Porch','Balcony','Sign']};
const state = {preset:'house', node:'footprint', view:'34', cfg:null, changed:{}};
const $ = (s)=>document.querySelector(s);
const clone = (o)=>JSON.parse(JSON.stringify(o));
const merge=(a,b)=>{for(const k in b){ if(b[k]&&typeof b[k]==='object'&&!Array.isArray(b[k])) a[k]=merge(a[k]||{},b[k]); else a[k]=b[k];} return a;};
function defaults(){const d={};for(const g of ORDER){d[g]={};for(const k in SCHEMA.properties[g].properties) d[g][k]=SCHEMA.properties[g].properties[k].default;}return d;}
function load(name){state.preset=name;state.cfg=merge(defaults(),clone(PRESETS[name].config));state.changed={};render();}
function setVal(g,k,v){state.cfg[g][k]=v;applyRules();state.dirty=true;render(false);}
function applyRules(){const c=state.cfg,notes=[];state.changed={};
 const mark=(g,why)=>{state.changed[g]=true;notes.push(why)};
 if(c.openings.door!=='none'&&c.storeys.courses<6){c.storeys.courses=6;mark('storeys','Courses raised to 6: the door is six courses tall (door-height)')}
 if(c.roof.style!=='flat'&&c.roof.style!=='none'&&c.footprint.shape!=='rect'){c.roof.style='flat';mark('roof','L, U and courtyard footprints get a flat roof (pitched-needs-rect)')}
 if(c.corners.style==='towers'&&(c.roof.style==='gable'||c.roof.style==='hip')){c.roof.style='flat';mark('roof','Towers need a flat roof: a pitched one would collide (towers-flat)')}
 if(c.roof.style!=='flat'&&c.roof.parapet!=='none'){c.roof.parapet='none';mark('roof','Parapets only exist on flat roofs (parapet-flat)')}
 if(c.roof.style!=='gable'&&c.roof.style!=='hip'&&c.details.chimney!=='none'){c.details.chimney='none';mark('details','A chimney needs a gable or hip roof (chimney-pitched)')}
 if(c.details.sign!=='none'&&c.storeys.courses<7){c.storeys.courses=7;mark('storeys','Courses raised to 7 for the sign band (sign-shop)')}
 if(c.footprint.shape!=='rect'&&c.storeys.setback){c.storeys.setback=0;mark('storeys','Setbacks only apply to rectangular footprints (setback-rect)')}
 state.notes=notes;}
function field(g,k,spec){const v=state.cfg[g][k];const wrap=document.createElement('div');wrap.className='field';
 const lab=document.createElement('label');lab.textContent=k.replace(/_/g,' ');wrap.append(lab);let ctl;
 if(spec['x-colour']){ctl=document.createElement('div');ctl.className='sw';const i=document.createElement('i');const s=document.createElement('select');
  COLOURS.forEach(([n,hex])=>{const o=document.createElement('option');o.value=n;o.textContent=n;if(n===v)o.selected=true;s.append(o)});
  const hex=(COLOURS.find(c=>c[0]===v)||[0,'#888'])[1];i.style.background=hex;s.onchange=()=>setVal(g,k,s.value);ctl.append(i,s);}
 else if(spec.enum){ctl=document.createElement('div');ctl.className='seg';spec.enum.forEach(e=>{const b=document.createElement('button');b.textContent=String(e);b.setAttribute('aria-pressed',String(e===v));b.onclick=()=>setVal(g,k,e);ctl.append(b)})}
 else if(spec.type==='boolean'){ctl=document.createElement('button');ctl.className='switch';ctl.setAttribute('role','switch');ctl.setAttribute('aria-checked',String(!!v));ctl.onclick=()=>setVal(g,k,!v)}
 else if(spec.type==='integer'){ctl=document.createElement('div');ctl.className='range';const r=document.createElement('input');r.type='range';r.min=spec.minimum;r.max=spec.maximum;r.value=v;const n=document.createElement('input');n.type='number';n.min=spec.minimum;n.max=spec.maximum;n.value=v;
  r.oninput=()=>{n.value=r.value};r.onchange=()=>setVal(g,k,+r.value);n.onchange=()=>setVal(g,k,Math.max(spec.minimum,Math.min(spec.maximum,+n.value)));ctl.append(r,n)}
 else if(spec.type==='array'){ctl=document.createElement('div');ctl.className='seg';spec.items.enum.forEach(e=>{const on=v.includes(e);const b=document.createElement('button');b.textContent=e;b.setAttribute('aria-pressed',String(on));b.onclick=()=>setVal(g,k,on?v.filter(x=>x!==e):[...v,e]);ctl.append(b)})}
 wrap.append(ctl);if(spec.description){const h=document.createElement('div');h.className='hint';h.textContent=spec.description;wrap.append(h)}return wrap;}
function render(full=true){const f=$('#form');f.replaceChildren();const g=state.node;const spec=SCHEMA.properties[g];
 const h=document.createElement('h2');h.textContent=TITLE[g];const p=document.createElement('p');p.className='about';p.textContent=spec.description;f.append(h,p);
 for(const k in spec.properties) f.append(field(g,k,spec.properties[k]));
 const tree=$('#tree');tree.replaceChildren();
 ORDER.forEach(n=>{const li=document.createElement('li');const b=document.createElement('button');b.className='node';b.setAttribute('aria-current',String(n===g));
  const d=document.createElement('span');d.className='dot';if(state.changed[n])d.style.background='var(--danger)';b.append(d,TITLE[n]);
  if(n==='storeys'){const s=document.createElement('small');s.className='mono';s.textContent='x'+state.cfg.storeys.count;b.append(s)}
  b.onclick=()=>{state.node=n;render()};li.append(b);
  if(CHILD[n]){const ul=document.createElement('ul');CHILD[n].forEach(c=>{const l=document.createElement('li');const bb=document.createElement('button');bb.className='node';bb.textContent=c;bb.onclick=()=>{state.node=n;render()};l.append(bb);ul.append(l)});li.append(ul)}
  tree.append(li)});
 const chips=$('#presetChips');chips.replaceChildren();Object.keys(PRESETS).forEach(n=>{const b=document.createElement('button');b.className='chip';b.textContent=PRESETS[n].title;b.setAttribute('aria-pressed',String(n===state.preset&&!state.dirty));b.onclick=()=>{state.dirty=false;load(n)};chips.append(b)});
 const img=$('#preview');const suffix=state.view==='34'?'34':'front';img.src='../renders/'+state.preset+'-'+suffix+'.png';$('#previewTag').textContent=state.dirty?state.preset+' + your changes (render of nearest preset)':state.preset;
 $('#status').textContent=state.dirty?'out of date: press Regenerate':'up to date';$('#status').className='badge '+(state.dirty?'warn':'ok');
 $('#notes').replaceChildren(...(state.notes||[]).map(t=>{const d=document.createElement('div');d.className='note';d.textContent=t;return d}));
 $('#json').textContent=JSON.stringify(state.cfg,null,1);$('#stats').textContent='~'+PRESETS[state.preset].parts+' parts';
 document.querySelectorAll('#viewFront,#view34').forEach(b=>b.setAttribute('aria-pressed',String((b.id==='view34')===(state.view==='34'))));}
$('#regen').onclick=()=>{state.dirty=false;$('#status').textContent='regenerated in ~1 s';render(false);$('#status').textContent='regenerated in ~1 s'};
$('#viewFront').onclick=()=>{state.view='front';render(false)};$('#view34').onclick=()=>{state.view='34';render(false)};
$('#theme').onclick=()=>{const h=document.documentElement;h.dataset.theme=h.dataset.theme==='dark'?'light':'dark'};
$('#savePreset').onclick=()=>{const n=prompt('Name this preset','my building');if(n){PRESETS[n]={title:n,config:clone(state.cfg),parts:PRESETS[state.preset].parts};state.preset=n;state.dirty=false;render()}};
const NLR=[[/tower.*(each|every|all) corner/,'corners',{style:'towers',tower_at:'all'}],[/arch/,'openings',{ground_windows:'arch',upper_windows:'arch'}],[/(three|3) stor/,'storeys',{count:3}],[/(two|2) stor/,'storeys',{count:2}],[/flat roof/,'roof',{style:'flat'}],[/battlement/,'roof',{style:'flat',parapet:'battlement'}],[/round tower/,'corners',{style:'towers',tower_shape:'round'}],[/chimney/,'details',{chimney:'right'}],[/porch/,'details',{porch:true}]];
$('#nlGo').onclick=()=>{const t=$('#nl').value.toLowerCase();const hit=[];NLR.forEach(([re,g,patch])=>{if(re.test(t)){Object.assign(state.cfg[g],patch);hit.push(g+': '+JSON.stringify(patch))}});
 const m=t.match(/make the (roof|walls?|door) (\w+( \w+)?)/);if(m){const key={roof:['roof','colour'],wall:['walls','colour'],walls:['walls','colour'],door:['openings','door_colour']}[m[1]];if(COLOURS.some(c=>c[0]===m[2])){state.cfg[key[0]][key[1]]=m[2];hit.push(key.join('.')+' = '+m[2])}}
 $('#nlOut').textContent=hit.length?'Understood: '+hit.join('; '):'Nothing matched. The AI route would handle this one.';if(hit.length){applyRules();state.dirty=true;render()}};
load('house');
</script></body></html>'''


def main():
    schema = grammar.SCHEMA
    presets = {}
    for name, p in grammar.PRESETS.items():
        parts = grammar.generate(p['config'])['stats']['parts']
        presets[name] = dict(title=p['title'], config=p['config'], parts=parts)
    colours = []
    seen = set()
    hexes = {0: '#1b2a34', 1: '#1e5aa8', 2: '#00852b', 4: '#c91a09', 14: '#f2cd37', 15: '#ffffff', 19: '#e4cd9e', 25: '#fe8a18',
             70: '#582a12', 71: '#a0a5a9', 72: '#6c6e68', 288: '#184632', 272: '#0d325b', 320: '#720e0f', 308: '#352100',
             484: '#91501c', 47: '#d8eaf5', 28: '#958a73', 335: '#a95b4f', 10: '#4b9f4a', 85: '#3f3691', 26: '#923978', 322: '#36aebf'}
    for name, code in NAMES.items():
        if code in hexes:
            colours.append([name, hexes[code]])
    for name, hexv in (('sand red', '#a95b4f'), ('dark tan', '#958a73'), ('dark green', '#184632'), ('dark red', '#720e0f'), ('green', '#00852b')):
        if not any(c[0] == name for c in colours):
            colours.append([name, hexv])
    html = (PAGE.replace('__THEME__', THEME).replace('__SCHEMA__', json.dumps(schema))
            .replace('__PRESETS__', json.dumps(presets)).replace('__COLOURS__', json.dumps(colours))
            .replace('__RULES__', json.dumps(grammar.CONSTRAINTS)))
    (HERE / 'index.html').write_text(html, encoding='utf-8')
    print('wrote', HERE / 'index.html', len(html), 'bytes')


if __name__ == '__main__':
    main()
