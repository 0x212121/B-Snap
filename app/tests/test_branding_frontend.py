"""Execute the branding draft/save workflow without a browser or application DB."""

import shutil
import subprocess

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override full-schema setup: this test executes only JavaScript in Node.js."""


def test_branding_preview_draft_upload_and_save_workflow():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the frontend workflow check")
    source = Path(__file__).resolve().parents[2] / "static/js/branding.js"
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const nodes = new Map();
function element(id) {
  const classes = new Set();
  const node = {value:'', dataset:{}, textContent:'', hidden:false, disabled:false, events:{},
    style:{setProperty(name,value){this[name]=value;}},
    classList:{add(name){classes.add(name);},contains(name){return classes.has(name);},toggle(name,on){on?classes.add(name):classes.delete(name);}},
    addEventListener(event,fn){this.events[event]=fn;},
    setAttribute(name,value){this[name]=value;},removeAttribute(name){delete this[name];},
    querySelector(){return this.label || (this.label={textContent:''});},click(){this.clicked=true;}};
  nodes.set(id,node); return node;
}
const initial = {name:'Acme',tagline:'Welcome',logo:'/old.png',palette:'blue'};
const defaults = {name:'B-Snap',tagline:'',logo:'/default.png',palette:'blue'};
const palettes = {blue:{primary:'#2563eb',hover:'#1d4ed8'},teal:{primary:'#0f766e',hover:'#115e59'}};
element('branding-data').textContent=JSON.stringify({initial,defaults,palettes,maxLogoSize:512*1024});
const inputs = Object.fromEntries(Object.entries(initial).map(([key,value])=>[key,Object.assign(element(key),{value})]));
const form=element('branding-form');
form.elements={namedItem:name=>inputs[name]}; form.reportValidity=()=>true;
for (const id of ['branding-preview','branding-save','branding-revert','branding-defaults','branding-clear-upload','branding-filename','branding-status','branding-fields','branding-preview-tagline','branding-upload-button']) element(id);
const upload=element('branding-upload'); upload.files=[];
Object.defineProperty(upload,'value',{set(){this.files=[];},get(){return '';}});
const names=[element('name-preview'),element('login-name-preview')];
const logos=[element('logo-preview'),element('login-logo-preview')];
const modes=['light','dark'].map(mode=>Object.assign(element(mode),{dataset:{previewMode:mode}}));
const windowEvents={}, requests=[], errors=[], storage=new Map(), revoked=[];
let resolveRequest, reloads=0;
const context=vm.createContext({
  document:{getElementById:id=>nodes.get(id),documentElement:element('html'),querySelectorAll:selector=>({
    '[data-branding-preview-name]':names,'[data-branding-preview-logo]':logos,'[data-preview-mode]':modes
  }[selector] || [])},
  window:{BSnapI18n:{t:value=>value},addEventListener:(event,fn)=>{windowEvents[event]=fn;},location:{reload(){reloads++;}}},
  URL:{createObjectURL:()=> 'blob:preview',revokeObjectURL:url=>revoked.push(url)},
  Toast:{error:message=>errors.push(message),success(){}},
  sessionStorage:{setItem:(key,value)=>storage.set(key,value),getItem:key=>storage.get(key),removeItem:key=>storage.delete(key)},
  FormData:class {
    constructor(){assert.equal(nodes.get('branding-fields').disabled,false,'Capture controls before disabling');this.values=Object.fromEntries(Object.entries(inputs).map(([key,input])=>[key,input.value]));}
  },
  fetch:(url,options)=>{requests.push({url,...options});return new Promise(resolve=>{resolveRequest=resolve;});}
});
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
const save=nodes.get('branding-save');
(async()=>{
  assert.equal(save.disabled,true);
  inputs.name.value='<img src=x onerror=unsafe()>';form.events.input();
  assert.equal(names[0].textContent,inputs.name.value,'Preview names are text');
  assert.equal(save.disabled,false);
  let prevented=false;windowEvents.beforeunload({preventDefault(){prevented=true;}});assert.equal(prevented,true);
  nodes.get('branding-defaults').events.click();
  assert.equal(inputs.name.value,'B-Snap');assert.equal(requests.length,0,'Defaults only change draft');
  nodes.get('branding-revert').events.click();assert.equal(inputs.name.value,'Acme');assert.equal(save.disabled,true);
  nodes.get('branding-upload-button').events.click();assert.equal(upload.clicked,true);
  upload.files=[{name:'new.png',size:100}];upload.events.change();assert.equal(logos[0].src,'blob:preview');
  assert.equal(save.disabled,false);nodes.get('branding-clear-upload').events.click();assert.equal(logos[0].src,'/old.png');
  assert.ok(revoked.includes('blob:preview'));
  upload.files=[{name:'oversized.png',size:512*1024+1}];upload.events.change();assert.equal(upload.files.length,0);assert.equal(save.disabled,true);
  modes[1].events.click();assert.equal(nodes.get('branding-preview').dataset.mode,'dark');assert.equal(save.disabled,true);
  inputs.name.value='Updated';form.events.input();
  const pending=form.events.submit({preventDefault(){}});
  assert.equal(nodes.get('branding-fields').disabled,true);
  await form.events.submit({preventDefault(){}});assert.equal(requests.length,1,'Prevent duplicate submits');
  assert.equal(requests[0].body.values.name,'Updated');
  resolveRequest({ok:false,json:async()=>({detail:[{msg:'Upload rejected'}]})});await pending;
  assert.equal(errors.at(-1),'Upload rejected');assert.equal(nodes.get('branding-fields').disabled,false);
  assert.equal(inputs.name.value,'Updated','Failed saves keep the draft');
  inputs.palette.value='teal';form.events.input();assert.equal(nodes.get('branding-preview').style['--ui-primary'],'#0f766e');
  const success=form.events.submit({preventDefault(){}});
  resolveRequest({ok:true,json:async()=>({message:'Branding saved.'})});await success;
  assert.equal(requests[1].body.values.palette,'teal');assert.equal(reloads,1);assert.equal(storage.get('bsnap.branding.saved'),'1');
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node, "-e", harness, str(source)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
