"""Check shared popup localization with an isolated DOM; no database schema is needed."""

from __future__ import annotations

import shutil
import subprocess

from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> None:
    """Override the shared SQLite schema fixture for this isolated JavaScript check."""


def test_shared_sweetalert_localizes_dynamic_content_and_preserves_inputs() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the frontend check")
    root = Path(__file__).resolve().parents[2]
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const listeners = new Map();
class Element {
  constructor(texts = [], attributes = {}) {
    this.nodeType = 1; this.attributes = attributes; this.value = 'typed input';
    this.nodes = texts.map(nodeValue => ({nodeType:3, nodeValue, parentElement:this}));
  }
  closest(selector) {
    return (selector === '.swal2-popup' ? this.popup : this.skip) ? this : null;
  }
  hasAttribute(name) { return name in this.attributes; }
  getAttribute(name) { return this.attributes[name]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  querySelectorAll() { return this.children || []; }
  querySelector() { return null; }
}
const body = new Element();
const document = {
  body, documentElement:{lang:'en', classList:{contains(){return false;}, remove(){}}},
  createTreeWalker(root) {
    const nodes = [root, ...root.querySelectorAll()].flatMap(el => el.nodes);
    return {nextNode(){return nodes.shift() || null;}};
  },
  getElementById(){return null;}, addEventListener(){}
};
const window = {
  addEventListener(name, callback){listeners.set(name, callback);},
  removeEventListener(name, callback){if (listeners.get(name) === callback) listeners.delete(name);},
  dispatchEvent(event){listeners.get(event.type)?.(event);}
};
let options;
const Swal = {fire(value){options=value;return Promise.resolve({isConfirmed:false});}, update(){}};
const context = vm.createContext({window,document,Swal,Element,Node:{TEXT_NODE:3,ELEMENT_NODE:1},
  NodeFilter:{SHOW_TEXT:4},CustomEvent:class {constructor(type){this.type=type;}},
  localStorage:{getItem(){return 'en';},setItem(){}},getSwalThemeConfig(){return {};}});
vm.runInContext(fs.readFileSync('static/js/i18n.js','utf8'), context);
const base = fs.readFileSync('templates/base.html','utf8');
const start = base.indexOf('function themedSwal(');
vm.runInContext(base.slice(start, base.indexOf('function showGlobalConfirm(', start)), context);
window.toggleLanguage();
const name = 'CAM [A].* "North"  $& {name}';
const dynamic = `Delete ALL snapshots for ${name}? This action cannot be undone.`;
const texts = ['Delete All Snapshots?', dynamic, 'Stored Hash: abc123',
  'Integrity check FAILED - file may be corrupted or tampered',
  'Confirmation must match "NVR [1].*".', 'Please select a CSV file', 'Delete this snapshot?'];
const popup = new Element(texts, {placeholder:'Enter your password'});
popup.popup = true;
const fileInput = new Element([], {type:'file'});
fileInput.value = 'selected.csv';
const code = new Element(['email,nickname,group_name,locations']); code.skip = true;
popup.children = [fileInput,code]; body.children = [popup];
let renders = 0, opens = 0;
context.themedSwal({title:'Delete All Snapshots?',didRender(){renders++;},didOpen(){opens++;}});
options.didRender(popup); options.didOpen(popup);
assert.equal(renders,1); assert.equal(opens,1);
assert.equal(popup.nodes[0].nodeValue,'Hapus Semua Snapshot?');
assert.equal(popup.nodes[1].nodeValue,`Hapus SEMUA snapshot untuk ${name}? Tindakan ini tidak dapat dibatalkan.`);
assert.equal(popup.nodes[2].nodeValue,'Hash Tersimpan: abc123');
assert.equal(popup.nodes[4].nodeValue,'Konfirmasi harus sesuai dengan "NVR [1].*".');
assert.equal(popup.nodes[5].nodeValue,'Pilih file CSV');
assert.equal(popup.nodes[6].nodeValue,'Hapus snapshot ini?');
assert.equal(window.BSnapI18n.t('Deleted'),'Dihapus');
assert.equal(popup.attributes.placeholder,'Masukkan kata sandi Anda');
assert.equal(popup.value,'typed input'); assert.equal(fileInput.value,'selected.csv');
assert.equal(code.nodes[0].nodeValue,'email,nickname,group_name,locations');
window.toggleLanguage();
assert.deepEqual(popup.nodes.map(node => node.nodeValue),texts);
assert.equal(popup.attributes.placeholder,'Enter your password');
window.toggleLanguage();
assert.equal(popup.nodes[1].nodeValue,`Hapus SEMUA snapshot untuk ${name}? Tindakan ini tidak dapat dibatalkan.`);
// A popup whose caller already localized the message must also return to English.
const prelocalized = new Element(['Jalankan "Job [1]" sekarang?', 'Jalankan Sekarang']);
prelocalized.popup = true;
body.children = [prelocalized]; options.didRender(prelocalized);
window.toggleLanguage();
assert.deepEqual(prelocalized.nodes.map(node => node.nodeValue),['Run "Job [1]" now?', 'Run Now']);
assert.equal(window.BSnapI18n.t('Unrecognized backend detail'),'Unrecognized backend detail');
// Dynamic templates do not interpret regex metacharacters or substitute data recursively.
window.toggleLanguage();
assert.equal(window.BSnapI18n.t('Email sent to 12 recipient(s)'),'Email dikirim ke 12 penerima');
assert.equal(window.BSnapI18n.t('Failed to generate report: 500 $& [detail]'),
  'Gagal membuat laporan: 500 $& [detail]');
for (const dark of [false,true]) {
  document.documentElement.classList.contains = () => dark;
  context.themedSwal({title:'Existing input dialog'});
  const input = {classList:{remove(){},add(value){this.value=value;}},style:{}};
  options.didOpen({querySelector(){return input;}});
  assert.equal(input.classList.value,dark ? 'swal2-input-dark':'swal2-input-light');
}
console.log('Shared popup i18n regression checks passed');
"""
    result = subprocess.run(
        [node, "-"], input=script, cwd=root, text=True, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
