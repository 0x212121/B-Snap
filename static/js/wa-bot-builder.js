(() => {
  const list = document.getElementById('commandList');
  const emptyState = document.getElementById('emptyState');
  const editor = document.getElementById('commandEditor');
  const form = document.getElementById('commandForm');
  const tokenSelect = document.getElementById('apiTokenId');
  const adminActions = ['edit','token_check','whitelist_add','whitelist_remove'];
  const privateOnlyActions = ['edit','token_check','whitelist_add','whitelist_remove'];
  const actionLabels = {
    text: 'Reply with text',
    help: 'Show help',
    ping_test: 'Test bot',
    bot_status: 'Check bot connectivity',
    ping: 'Ping host',
    cctv: 'Send latest snapshot',
    snap: 'Capture new snapshot',
    api_flow: 'Internal B-Snap API flow',
    edit: 'Edit camera data',
    token_check: 'View token',
    whitelist_add: 'Add to whitelist',
    whitelist_remove: 'Remove from whitelist'
  };
  const systemActionSamples = {
    cctv: {result:{status_code:200,body:[{camera:'CAM-M5-01',ip:'192.168.1.10',timestamp:'2026-10-05T10:30:00Z',filename:'snapshot.jpg',url:'/snapshot/file/snapshot.jpg',lat:'-3.12',long:'115.22'}]}},
    snap: {result:{status_code:200,body:[{camera:'CAM-M5-01',ip:'192.168.1.10',timestamp:'2026-10-05T10:30:00Z',filename:'snapshot.jpg',url:'/snapshot/file/snapshot.jpg',uptime:'2d 4h',lat:'-3.12',long:'115.22'}]}},
    ping: {ping:{status_code:200,body:{target:'camera.example',online:true,replies:['Reply from camera.example: time=12.4ms','Reply from camera.example: time=11.8ms']}}},
    edit: {result:{status_code:200,body:{camera:'CAM-M5-01',hostname:'CAM-M5-01',ip:'192.168.1.10',port:80,status:'Active',group:'North'}}},
    token_check: {result:{status_code:200,body:[{username:'admin',token:'abcde…wxyz',expires_at:'2026-12-31T23:59:59Z'}]}},
    whitelist_add: {result:{status_code:200,body:{phone:'628123456789',action:'added',success:true}}},
    whitelist_remove: {result:{status_code:200,body:{phone:'628123456789',action:'removed',success:true}}},
    help: {result:{status_code:200,body:{commands:[{trigger:'/help',description:'Show available commands'},{trigger:'/snap',description:'Capture a new snapshot'}]}}},
    bot_status: {result:{status_code:200,body:{status:'Online',connected:true,logged_in:true,response_time_ms:120,error:''}}},
    ping_test: {}
  };
  let commands = [];
  let draft = null;
  let originalId = null;
  let lastStageTemplateTarget = null;


  const t = (en, id) => document.documentElement.lang === 'id' ? id : en;
  let savedTokenId = '', editorBaseline = '', editorReturnFocus = null, previousBodyOverflow = '';
  let saving = false, executing = false, monitorLoading = false, lastMonitorRefresh = null;
  let messageRows = [], previewRequest = 0, loaded = false, previewTimer = null;
  function collectCommand() {
    if (!draft) return null;
    const result = structuredClone(draft);
    for (const key of ['name','trigger','description','response','action','chat_scope','response_type','response_image_source','response_video_source']) result[key] = form.elements[key].value;
    result.trigger = result.trigger.trim().toLowerCase();
    for (const key of ['aliases','required_params']) result[key] = form.elements[key].value.split(',').map(value=>value.trim()).filter(Boolean);
    result.aliases = result.aliases.map(value=>value.toLowerCase());
    result.enabled = form.elements.enabled.checked; result.quote_reply = form.elements.quote_reply.checked;
    result.processing_delay_seconds = Number(form.elements.processing_delay_seconds.value);
    result.role = adminActions.includes(result.action) ? 'admin' : 'user';
    if (privateOnlyActions.includes(result.action)) result.chat_scope = 'private';
    return result;
  }
  function editorDirty() { return Boolean(draft && editorBaseline && JSON.stringify(collectCommand()) !== editorBaseline); }
  function commandConflicts(command) {
    const triggers = [command.trigger, ...command.aliases].filter(Boolean);
    const duplicate = triggers.find((value,index)=>triggers.indexOf(value)!==index || commands.some(item=>item.id!==command.id && [item.trigger,...(item.aliases||[])].some(other=>other.toLowerCase()===value)));
    return duplicate ? `${t('Trigger or alias already used','Trigger atau alias sudah dipakai')}: ${duplicate}` : '';
  }
  function updateDirtyState() {
    document.getElementById('apiSettingsState').textContent = tokenSelect.value !== savedTokenId ? t('Unsaved API settings','Pengaturan API belum disimpan') : t('API settings saved','Pengaturan API tersimpan');
    document.getElementById('saveCommands').disabled = saving || tokenSelect.value === savedTokenId;
    document.getElementById('editorSaveState').textContent = executing ? t('Executing action…','Menjalankan aksi…') : saving ? t('Saving…','Menyimpan…') : editorDirty() ? t('Unsaved command changes','Perubahan command belum disimpan') : t('No unsaved changes','Tidak ada perubahan');
    document.getElementById('cancelEditor').disabled = saving || executing;
    document.getElementById('closeEditor').disabled = saving || executing;
  }
  function filteredCommands() {
    const query = document.getElementById('commandSearch').value.trim().toLowerCase();
    const status = document.getElementById('commandStatusFilter').value;
    const action = document.getElementById('commandActionFilter').value;
    return commands.filter(item=>(!query || [item.name,item.trigger,...(item.aliases||[])].some(value=>(value||'').toLowerCase().includes(query))) && (!status || Boolean(item.enabled)===(status==='active')) && (!action || item.action===action));
  }
  function selectEditorTab(section) {
    const conversation = form.querySelector('[data-conversation-panel]');
    if (conversation) {
      if (section === 'test') form.querySelector('[data-test-panel]').insertBefore(conversation, form.querySelector('[data-test-controls]'));
      else form.querySelector('[data-response-panel]').appendChild(conversation);
    }
    form.querySelectorAll('[data-editor-section]').forEach(panel=>panel.hidden=panel.dataset.editorSection!==section);
    document.querySelectorAll('[data-editor-tab]').forEach(tab=>{ const active=tab.dataset.editorTab===section; tab.setAttribute('aria-selected',String(active)); tab.tabIndex=active?0:-1; });
  }
  function resetPreviewData() {
    const samples = structuredClone(systemActionSamples[form.elements.action.value] || {});
    if (form.elements.action.value === 'api_flow') for (const step of draft?.flow || []) samples[step.name]={status_code:200,body:{}};
    form.querySelector('[data-preview-context]').value = JSON.stringify(samples,null,2);
    form.querySelector('[data-chat-preview]').replaceChildren();
    form.querySelector('[data-preview-state]').textContent = t('Sample data only. Edit the JSON to preview your API fields.','Data contoh saja. Edit JSON untuk melihat hasil field API.');
    form.querySelector('[data-test-response-preview]').classList.add('hidden');
    previewRequest++; scheduleConversationPreview();
  }
  async function renderConversation(steps, responseText, outcome, actual = false) {
    const requestId = ++previewRequest;
    const command = collectCommand();
    const argument = form.querySelector('[data-test-argument]').value || (actual ? '' : (command.required_params.length ? command.required_params.map(name=>/duration|seconds/i.test(name) ? '30' : 'CAM-01').join(' ') : 'CAM-01'));
    const sender = form.querySelector('[data-test-sender]').value || (actual ? '' : '628123456789');
    const response = await fetch('/api/admin/wa-bot/preview', {method:'POST',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({command,steps,argument,sender,outcome,response_text:responseText ?? null})});
    const data = await readApiJson(response,'Render conversation');
    if (!response.ok) throw new Error(data.detail || 'Preview failed');
    if (requestId!==previewRequest || !draft) return;
    const chat = form.querySelector('[data-chat-preview]'); chat.replaceChildren();
    const incoming = document.createElement('div'); incoming.className = 'wa-chat-incoming'; incoming.textContent = `${command.trigger || '/command'} ${argument}`.trim(); chat.appendChild(incoming);
    for (const item of data.messages || []) {
      const bubble = document.createElement('div'); bubble.className = 'wa-chat-bubble';
      const title = document.createElement('p'); title.className = 'text-xs font-semibold mb-2'; title.textContent = item.stage==='processing' ? `${t('Processing','Diproses')} · ${data.processing_delay_seconds}s` : item.stage==='fallback' ? t('Fallback','Fallback') : t('Success','Berhasil'); bubble.appendChild(title);
      if (item.media_type!=='text') { const media=document.createElement('p'); media.className='wa-chat-media'; media.textContent=`${item.media_type==='video' ? 'Video' : t('Image','Gambar')}: ${item.source || t('No source','Sumber belum diisi')}`; bubble.appendChild(media); }
      const text=document.createElement('p'); text.className='whitespace-pre-wrap break-words'; text.textContent=item.text || t('(No caption)','(Tanpa caption)'); bubble.appendChild(text); chat.appendChild(bubble);
    }
    form.querySelector('[data-preview-state]').textContent=actual ? t('Preview from the executed action. Processing messages are shown as configured; they may be skipped if the action finishes before the delay.','Pratinjau hasil aksi nyata. Pesan processing ditampilkan sesuai konfigurasi; bisa dilewati jika aksi selesai sebelum delay.') : t('Sample preview. Media sources are displayed without loading or sending media. Processing messages depend on the configured delay.','Pratinjau contoh. Sumber media ditampilkan tanpa memuat atau mengirim media. Pesan processing mengikuti delay.');
  }
  async function refreshConversationPreview() {
    if (!draft || executing) return;
    const requestId = previewRequest;
    try {
      const steps = JSON.parse(form.querySelector('[data-preview-context]').value || '{}');
      if (!steps || Array.isArray(steps) || typeof steps !== 'object') throw new Error(t('Preview JSON must be an object','JSON pratinjau harus berupa objek'));
      await renderConversation(steps, null, form.querySelector('[data-preview-outcome]').value);
    } catch (error) {
      if (draft && requestId + 1 >= previewRequest) {
        form.querySelector('[data-preview-state]').textContent = error.message;
        form.querySelector('[data-chat-preview]').replaceChildren();
      }
    }
  }
  function scheduleConversationPreview() {
    if (!draft || executing) return;
    clearTimeout(previewTimer); previewRequest++;
    form.querySelector('[data-preview-state]').textContent = t('Updating preview…','Memperbarui pratinjau…');
    previewTimer = setTimeout(refreshConversationPreview, 350);
  }
  form.querySelector('[data-preview]').addEventListener('click', () => {
    clearTimeout(previewTimer); refreshConversationPreview();
  });
  form.addEventListener('input', scheduleConversationPreview);
  form.addEventListener('change', scheduleConversationPreview);
  form.addEventListener('click', event => {
    if (event.target.closest('[data-add-snap-message], [data-snap-message-list] button, [data-add-step], [data-flow-list] button')) scheduleConversationPreview();
  });
  function renderMessages() {
    const phone=document.getElementById('messagePhone').value.trim().toLowerCase();
    const command=document.getElementById('messageCommand').value.trim().toLowerCase();
    const status=document.getElementById('messageStatus').value;
    const from=document.getElementById('messageFrom').value, to=document.getElementById('messageTo').value;
    const rows=messageRows.filter(item=>(!phone || (item.phone_number||'').toLowerCase().includes(phone)) && (!command || (item.command||'').toLowerCase().includes(command)) && (!status || (status==='failed' ? Boolean(item.error)||/fail|error|reject/i.test(item.status) : item.status===status)) && (!from || new Date(item.timestamp)>=new Date(from)) && (!to || new Date(item.timestamp)<=new Date(to)));
    const body=document.getElementById('waMessageRows'); body.replaceChildren();
    for (const item of rows) {
      const row=document.createElement('tr'); row.className='border-b align-top dark:border-gray-700';
      for (const value of [item.timestamp ? new Date(item.timestamp).toLocaleString(BSnapDates.locale()) : '',item.phone_number,item.direction,item.status,item.command || '',item.error || item.message || '']) {
        const cell=document.createElement('td'); cell.className='max-w-sm break-words p-2';
        if (row.children.length===3) { const badge=document.createElement('span'); badge.className=`ui-badge ${item.error || /fail|error|reject/i.test(item.status) ? 'ui-badge-critical' : item.status==='sent' ? 'ui-badge-success' : 'ui-badge-neutral'}`; badge.textContent=value; cell.appendChild(badge); }
        else { const text=document.createElement('span'); text.className='wa-message-summary'; text.textContent=value; cell.appendChild(text); }
        row.appendChild(cell);
      }
      const cell=document.createElement('td'); cell.className='p-2'; const details=document.createElement('button'); details.type='button'; details.className='ui-button ui-button-secondary'; details.textContent=t('Details','Detail');
      details.addEventListener('click',()=>themedSwal({title:t('Message details','Detail pesan'),text:[`ID: ${item.id}`,`${t('Time','Waktu')}: ${item.timestamp}`,`${t('Phone','Nomor')}: ${item.phone_number}`,`Direction: ${item.direction}`,`Status: ${item.status}`,`Command: ${item.command||'—'}`,`${t('Message','Pesan')}: ${item.message||'—'}`,`Error: ${item.error||'—'}`].join('\n'),customClass:{htmlContainer:'wa-message-detail'},confirmButtonText:t('Close','Tutup')}));
      cell.appendChild(details); row.appendChild(cell); body.appendChild(row);
    }
    if (!rows.length) { const row=body.insertRow(); const cell=row.insertCell(); cell.colSpan=7; cell.className='p-4'; cell.textContent=t('No messages match these filters.','Tidak ada pesan sesuai filter.'); }
  }
  const responsePanel = form.querySelector('[data-response-panel]');
  const responseFields = document.createElement('div'); responseFields.className = 'wa-response-fields';
  while (responsePanel.firstChild) responseFields.appendChild(responsePanel.firstChild);
  responsePanel.appendChild(responseFields);
  const testPanel=form.querySelector('[data-test-panel]'); form.appendChild(testPanel);
  const actionLabel=form.elements.action.closest('label'); form.querySelector('[data-flow-panel]').prepend(actionLabel);
  for (const [value,label] of Object.entries(actionLabels)) document.getElementById('commandActionFilter').add(new Option(label,value));
  for (const id of ['commandSearch','commandStatusFilter','commandActionFilter']) document.getElementById(id).addEventListener('input',renderList);
  for (const id of ['messagePhone','messageCommand','messageStatus','messageFrom','messageTo']) document.getElementById(id).addEventListener('input',renderMessages);
  document.getElementById('clearMessageFilters').addEventListener('click',()=>{for(const id of ['messagePhone','messageCommand','messageStatus','messageFrom','messageTo']) document.getElementById(id).value=''; renderMessages();});
  tokenSelect.addEventListener('change',updateDirtyState);
  for(const event of ['input','change','click']) form.addEventListener(event,()=>queueMicrotask(updateDirtyState));
  form.addEventListener('invalid',event=>{const section=event.target.closest('[data-editor-section]'); if(section) selectEditorTab(section.dataset.editorSection);},true);
  document.getElementById('cancelEditor').addEventListener('click',()=>closeEditor());
  document.querySelectorAll('[data-editor-tab]').forEach(tab=>tab.addEventListener('click',()=>selectEditorTab(tab.dataset.editorTab)));
  document.getElementById('editorTabs').addEventListener('keydown',event=>{
    const tabs=[...document.querySelectorAll('[data-editor-tab]')], index=tabs.indexOf(document.activeElement);
    if(index<0 || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault(); const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
    selectEditorTab(tabs[next].dataset.editorTab); tabs[next].focus();
  });
  editor.addEventListener('keydown',event=>{
    if(document.querySelector('.swal2-container')) return;
    if(event.key==='Escape'){event.preventDefault();closeEditor();}
    if(event.key==='Tab') {
      const focusable=[...editor.querySelectorAll('button,input,select,textarea,a,[tabindex="0"]')].filter(el=>!el.disabled && el.getClientRects().length && el.tabIndex>=0);
      const first=focusable[0],last=focusable[focusable.length-1];
      if(event.shiftKey && document.activeElement===first){event.preventDefault();last?.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first?.focus();}
    }
  });
  window.addEventListener('beforeunload',event=>{if(editorDirty() || tokenSelect.value!==savedTokenId){event.preventDefault();event.returnValue='';}});

  function selectTab(tab) {
    const commandsSelected = tab === 'commands';
    if (!commandsSelected) loadOperations().catch(()=>{});
    document.getElementById('commandsPanel').hidden = !commandsSelected;
    document.getElementById('messagesPanel').hidden = commandsSelected;
    document.getElementById('commandActions').hidden = !commandsSelected;
    const commandTab = document.getElementById('commandsTab');
    const messagesTab = document.getElementById('messagesTab');
    commandTab.setAttribute('aria-selected', String(commandsSelected));
    messagesTab.setAttribute('aria-selected', String(!commandsSelected));
    commandTab.tabIndex = commandsSelected ? 0 : -1;
    messagesTab.tabIndex = commandsSelected ? -1 : 0;
    commandTab.classList.toggle('border-blue-600', commandsSelected);
    commandTab.classList.toggle('border-transparent', !commandsSelected);
    commandTab.classList.toggle('text-blue-700', commandsSelected);
    commandTab.classList.toggle('text-gray-500', !commandsSelected);
    commandTab.classList.toggle('dark:text-blue-300', commandsSelected);
    commandTab.classList.toggle('dark:text-gray-400', !commandsSelected);
    messagesTab.classList.toggle('border-blue-600', !commandsSelected);
    messagesTab.classList.toggle('border-transparent', commandsSelected);
    messagesTab.classList.toggle('text-blue-700', !commandsSelected);
    messagesTab.classList.toggle('text-gray-500', commandsSelected);
    messagesTab.classList.toggle('dark:text-blue-300', !commandsSelected);
    messagesTab.classList.toggle('dark:text-gray-400', commandsSelected);
  }
  document.getElementById('commandsTab').addEventListener('click', () => selectTab('commands'));
  document.getElementById('messagesTab').addEventListener('click', () => selectTab('messages'));
  document.getElementById('commandsTab').closest('[role="tablist"]').addEventListener('keydown', event => {
    if (!['ArrowLeft','ArrowRight','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const commands = event.key === 'Home' || (event.key !== 'End' && document.activeElement.id === 'messagesTab');
    selectTab(commands ? 'commands' : 'messages');
    document.getElementById(commands ? 'commandsTab' : 'messagesTab').focus();
  });

  function appendIcon(button, name) {
    const template = document.getElementById(`waIcon${name}`);
    if (template) button.appendChild(template.content.cloneNode(true));
  }

  function showToast(type, message) { if (window.Toast && Toast[type]) Toast[type](message); }
  async function readApiJson(response, action) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.toLowerCase().includes('application/json')) {
      const detail = response.redirected
        ? 'The session may have expired; sign in again and reload this page.'
        : `The server returned HTTP ${response.status} instead of JSON.`;
      throw new Error(`${action}: ${detail}`);
    }
    return response.json();
  }
  function setRole(action) {
    form.querySelector('[data-role]').textContent = `Access required: ${adminActions.includes(action) ? 'WhatsApp admin' : 'whitelisted user'}`;
  }
  function setChatScope(action) {
    const select = form.elements.chat_scope;
    const note = form.querySelector('[data-chat-scope-note]');
    const forcedPrivate = privateOnlyActions.includes(action);
    const isIndonesian = document.documentElement.lang === 'id';
    if (forcedPrivate) select.value = 'private';
    select.disabled = forcedPrivate;
    note.textContent = forcedPrivate
      ? (isIndonesian ? 'Aksi admin sensitif ini hanya dapat dijalankan lewat japri.' : 'This sensitive admin action is restricted to private chats.')
      : (isIndonesian ? 'Command japri saja juga disembunyikan dari daftar /help di grup.' : 'Private-only commands are also hidden from group /help lists.');
  }
  function setResponseTemplateVisibility(action) {
    const usesTemplate = true;
    const isSnap = action === 'snap';
    const isIndonesian = document.documentElement.lang === 'id';
    const isVideo = form.elements.response_type.value === 'video';
    const isImage = form.elements.response_type.value === 'image';
    form.querySelector('[data-response-media]').classList.remove('hidden');
    form.querySelector('[data-response-image]').classList.toggle('hidden', !isImage);
    form.elements.response_image_source.required = isImage;
    form.querySelector('[data-response-video]').classList.toggle('hidden', !isVideo);
    form.elements.response_video_source.required = isVideo;
    form.querySelector('[data-response-type-label]').textContent = isIndonesian ? 'Jenis respons' : 'Response type';
    form.querySelector('[data-response-video-label]').textContent = isIndonesian ? 'ID atau URL video aman dari API' : 'Video ID or secure URL from API';
    form.querySelector('[data-response-image-label]').textContent = isIndonesian ? 'URL gambar atau URL snapshot aman' : 'Image URL or protected snapshot URL';
    form.querySelector('[data-response-image-hint]').textContent = isIndonesian ? 'Gunakan URL gambar HTTP(S) atau URL snapshot dari hasil aksi/API.' : 'Use an HTTP(S) image URL or a snapshot URL from the action/API result.';
    form.querySelector('[data-response-panel]').classList.toggle('hidden', !usesTemplate);
    form.querySelector('[data-snap-stage-panel]').classList.remove('hidden');
    form.elements.response.required = usesTemplate && action !== 'ping' && !isVideo && !isImage;
    if (action === 'bot_status' && !draft.response?.trim()) {
      draft.response = '{' + '{status_icon}' + '} Bot Status: {' + '{status}' + '}\n🕒 Response Time: {' + '{response_time}' + '}';
      form.elements.response.value = draft.response;
    }
    if (isSnap) {
      if (!Array.isArray(draft.processing_messages) || !draft.processing_messages.length) {
        draft.processing_messages = [{text:isIndonesian ? 'Snapshot untuk {{camera}} sedang diproses. Mohon tunggu.' : 'Snapshot capture for {{camera}} is in progress. Please wait.',image_source_type:'none',image_source:''}];
      }
      if (!Array.isArray(draft.failure_messages) || !draft.failure_messages.length) {
        draft.failure_messages = [{text:isIndonesian ? 'Gagal mengambil snapshot baru untuk {{camera}}. Mengirim snapshot terakhir jika tersedia.' : 'Could not capture a new snapshot for {{camera}}. Sending the latest available snapshot if one exists.',image_source_type:'last_snapshot',image_source:''}];
      }
      drawSnapMessages('processing_messages');
      drawSnapMessages('failure_messages');
    } else {
      if (!Array.isArray(draft.processing_messages)) draft.processing_messages = [];
      if (!Array.isArray(draft.failure_messages)) draft.failure_messages = [];
      drawSnapMessages('processing_messages');
      drawSnapMessages('failure_messages');
    }
    form.querySelector('[data-response-label]').textContent = isSnap && !isImage && !isVideo
      ? (isIndonesian ? 'Pesan berhasil (caption snapshot)' : 'Success message (snapshot caption)')
      : (isVideo ? (isIndonesian ? 'Caption video' : 'Video caption') : (isImage ? (isIndonesian ? 'Caption gambar' : 'Image caption') : 'WhatsApp response template'));
    form.querySelector('[data-processing-label]').textContent = isIndonesian ? 'Pesan saat processing' : 'Processing messages';
    form.querySelector('[data-processing-delay-label]').textContent = isIndonesian ? 'Delay processing (detik)' : 'Processing delay (seconds)';
    form.querySelector('[data-processing-delay-hint]').textContent = isIndonesian ? 'Kirim hanya jika proses masih berjalan. Pesan tertunda dibatalkan saat hasil akhir atau fallback siap. 0 mengirim langsung.' : 'Send only if the operation is still running. Pending messages are skipped when a final or failure response is ready. 0 sends immediately.';
    form.querySelector('[data-failure-label]').textContent = isIndonesian ? 'Pesan gagal / fallback' : 'Failure / fallback messages';
  }
  function drawSnapMessages(key) {
    if (!draft) return;
    const list = draft[key] || [];
    const container = form.querySelector(`[data-snap-message-list="${key}"]`);
    if (!container) return;
    container.replaceChildren();
    const isIndonesian = document.documentElement.lang === 'id';
    list.forEach((item, index) => {
      const card = document.createElement('div'); card.className = 'ui-card rounded-lg bg-gray-50 p-3 dark:bg-gray-900';
      const top = document.createElement('div'); top.className = 'mb-2 flex items-center justify-between text-xs font-medium';
      const title = document.createElement('span'); title.textContent = `${isIndonesian ? 'Pesan' : 'Message'} ${index + 1}`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ui-button ui-button-danger-quiet'; remove.textContent = isIndonesian ? 'Hapus' : 'Remove';
      remove.addEventListener('click', () => {
        if (list.length > 1) list.splice(index, 1);
        else list[0] = {text:'',image_source_type:'none',image_source:''};
        drawSnapMessages(key);
      });
      top.append(title, remove); card.appendChild(top);
      const text = document.createElement('textarea'); text.rows = 2; text.maxLength = 1000;
      text.placeholder = isIndonesian ? 'Isi pesan WhatsApp (opsional jika ada gambar)' : 'WhatsApp message (optional when an image is selected)';
      text.className = 'ui-control mb-2 w-full rounded border-gray-300 font-mono text-sm dark:border-gray-600 dark:bg-gray-800'; text.value = item.text || '';
      text.addEventListener('input', () => { item.text = text.value; }); card.appendChild(text);
      const sourceRow = document.createElement('div'); sourceRow.className = 'grid gap-2 sm:grid-cols-2';
      const select = document.createElement('select'); select.className = 'ui-control rounded border-gray-300 text-sm dark:border-gray-600 dark:bg-gray-800';
      const choices = [
        ['none', isIndonesian ? 'Tanpa gambar' : 'No image'],
        ['url', isIndonesian ? 'Gambar dari URL' : 'Image URL'],
        ...(draft.action === 'snap' || draft.action === 'api_flow' ? [['api', isIndonesian ? 'URL gambar dari hasil API' : 'Image URL from API result']] : []),
        ['last_snapshot', isIndonesian ? 'Snapshot terakhir tersimpan' : 'Latest saved snapshot'],
      ];
      choices.forEach(([value,label]) => select.add(new Option(label, value)));
      select.value = item.image_source_type || 'none';
      const source = document.createElement('input'); source.type = 'text'; source.maxLength = 1500;
      source.placeholder = select.value === 'api' ? '{{steps.camera.body.image_url}}' : 'https://example.com/image.jpg';
      source.className = 'ui-control rounded border-gray-300 text-sm dark:border-gray-600 dark:bg-gray-800'; source.value = item.image_source || '';
      const updateSource = () => {
        item.image_source_type = select.value;
        source.hidden = !['url','api'].includes(select.value);
        source.placeholder = select.value === 'api' ? '{{steps.camera.body.image_url}}' : 'https://example.com/image.jpg';
      };
      select.addEventListener('change', updateSource);
      source.addEventListener('input', () => { item.image_source = source.value; });
      updateSource(); sourceRow.append(select, source); card.appendChild(sourceRow); container.appendChild(card);
    });
  }
  function drawFlow() {
    const panel = form.querySelector('[data-flow-panel]');
    const customFlow = draft.action === 'api_flow';
    const snapImageFlow = draft.action === 'snap';
    const flowEditorVisible = customFlow || snapImageFlow;
    const hasPreview = true;
    const testControlsVisible = true;
    panel.classList.toggle('hidden', !hasPreview);
    panel.querySelector('[data-flow-editor]').classList.toggle('hidden', !flowEditorVisible);
    form.querySelector('[data-test-controls]').classList.toggle('hidden', !testControlsVisible);
    const isIndonesian = document.documentElement.lang === 'id';
    panel.querySelector('[data-flow-title]').lastChild.textContent = snapImageFlow
      ? (isIndonesian ? 'API flow untuk sumber gambar' : 'Image source API flow')
      : (customFlow ? 'API flow (GET / POST)' : (isIndonesian ? 'Variabel system action' : 'System action variables'));
    panel.querySelector('[data-flow-subtitle]').textContent = snapImageFlow
      ? (isIndonesian ? 'Gunakan hasil node untuk mengisi URL gambar API pada pesan processing atau fallback.' : 'Use node results to provide API image URLs in processing or fallback messages.')
      : (customFlow
      ? (isIndonesian ? 'Node internal B-Snap dijalankan berurutan, maksimal 8 node.' : 'Internal B-Snap nodes run in order, up to 8 nodes.')
      : (isIndonesian ? 'Contoh variabel tersedia langsung. Jalankan uji untuk melihat hasil success/error sebenarnya.' : 'Sample variables are available immediately. Execute a test to inspect actual success/error results.'));
    const executeButton = form.querySelector('[data-test-flow]');
    executeButton.classList.toggle('ui-button-danger', draft.action === 'whitelist_remove');
    executeButton.classList.toggle('ui-button-secondary', draft.action !== 'whitelist_remove');
    const executeLabel = customFlow
      ? (isIndonesian ? 'Jalankan aksi' : 'Execute action')
      : (isIndonesian ? 'Jalankan aksi' : 'Execute action');
    executeButton.replaceChildren(); appendIcon(executeButton,'Play'); executeButton.appendChild(document.createTextNode(executeLabel));
    form.querySelector('[data-test-description]').textContent = customFlow
      ? (isIndonesian ? 'Gunakan nilai contoh untuk {{argument}} dan {{sender}}. Uji ini menjalankan alur yang belum disimpan.' : 'Enter arguments and a whitelisted sender phone number. This test runs the current unsaved flow.')
      : (isIndonesian ? 'Uji menjalankan aksi sebenarnya, termasuk capture snapshot baru, edit kamera, atau perubahan whitelist. Nomor pengirim wajib terdaftar di whitelist; role dan grup mengikuti nomor tersebut. Pesan WhatsApp tidak dikirim. Hasil success/error tersedia sebagai variabel.' : 'This test executes the actual action, including new snapshot capture, camera edits, or whitelist changes. The sender must be whitelisted; its role and camera group apply. No WhatsApp messages are sent. Success/error fields become template variables.');
    const container = form.querySelector('[data-flow-list]');
    container.replaceChildren();
    const activeFlow = snapImageFlow ? (draft.image_flow || []) : (draft.flow || []);
    if (flowEditorVisible && !activeFlow.length) {
      const empty = document.createElement('p'); empty.className = 'text-xs text-gray-500'; empty.textContent = 'No nodes yet. Add an API node to start building the flow.'; container.appendChild(empty);
    }
    if (!flowEditorVisible) container.replaceChildren();
    activeFlow.forEach((step, index) => {
      const card = document.createElement('div');
      card.className = 'ui-card rounded-lg bg-gray-50 p-3 dark:bg-gray-900';
      const heading = document.createElement('div'); heading.className = 'mb-2 flex justify-between text-xs font-semibold';
      const title = document.createElement('span'); title.textContent = `Node ${index + 1}`;
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ui-button ui-button-danger-quiet inline-flex items-center gap-1.5'; appendIcon(remove, 'Delete'); remove.appendChild(document.createTextNode('Delete'));
      remove.addEventListener('click', () => { activeFlow.splice(index, 1); drawFlow(); });
      heading.append(title, remove); card.appendChild(heading);
      const methodLabel = document.createElement('label'); methodLabel.className = 'ui-label mb-2 block text-xs'; methodLabel.textContent = isIndonesian ? 'Metode HTTP' : 'HTTP method';
      const methodSelect = document.createElement('select'); methodSelect.className = 'ui-control mt-1 w-full rounded border-gray-300 dark:border-gray-600 dark:bg-gray-800';
      for (const method of ['GET', 'POST']) { const option = document.createElement('option'); option.value = method; option.textContent = method; methodSelect.appendChild(option); }
      methodSelect.value = step.method || 'GET';
      methodSelect.addEventListener('change', () => { step.method = methodSelect.value; drawFlow(); });
      methodLabel.appendChild(methodSelect); card.appendChild(methodLabel);
      const fields = [['name','Output name','camera'],['path','API path','/api/cctv/resolve-ip'],['params','Query params JSON','{"keyword":"{{argument}}","phone_number":"{{sender}}"}']];
      if ((step.method || 'GET') === 'POST') fields.push(['body',isIndonesian ? 'Body JSON' : 'JSON body','{"hostname":"{{argument}}","duration":30}']);
      for (const [key, label, placeholder] of fields) {
        const wrapper = document.createElement('label'); wrapper.className = 'ui-label mb-2 block text-xs'; wrapper.textContent = label;
        const isJson = key === 'params' || key === 'body';
        const input = document.createElement(isJson ? 'textarea' : 'input');
        input.className = 'ui-control mt-1 w-full rounded border-gray-300 font-mono dark:border-gray-600 dark:bg-gray-800'; input.placeholder = placeholder;
        if (isJson) { input.rows = 2; input.value = typeof step[key] === 'string' ? step[key] : JSON.stringify(step[key] || {}, null, 2); }
        else input.value = step[key] || '';
        input.addEventListener('input', () => { step[key] = input.value; });
        wrapper.appendChild(input); card.appendChild(wrapper);
      }
      container.appendChild(card);
    });
    if (!customFlow) showSystemActionVariables(draft.action);
  }
  function showSystemActionVariables(action) {
    const resultPanel = form.querySelector('[data-test-result]');
    const status = form.querySelector('[data-test-status]');
    const output = form.querySelector('[data-test-json]');
    const steps = systemActionSamples[action] || {};
    resultPanel.classList.remove('hidden');
    status.textContent = document.documentElement.lang === 'id'
      ? 'Contoh struktur success. Jalankan uji aksi untuk hasil success/error sebenarnya.'
      : 'Sample success structure. Execute the action test for actual success/error results.';
    status.className = 'mb-2 text-sm text-green-700 dark:text-green-300';
    output.textContent = JSON.stringify(steps, null, 2);
    const variables = form.querySelector('[data-test-variables]');
    showFlowVariables(steps);
    addVariableSuggestion(variables, 'argument', 'sample argument');
    addVariableSuggestion(variables, 'sender', '628123456789');
  }
  function newCommandId() {
    if (typeof window.crypto.randomUUID === 'function') {
      return `custom_${window.crypto.randomUUID()}`;
    }
    // getRandomValues also works on HTTP origins where randomUUID is unavailable.
    const bytes = window.crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    return `custom_${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function openEditor(command = null) {
    draft = command ? structuredClone(command) : {id:newCommandId(),name:'',trigger:'',aliases:[],required_params:[],action:'text',role:'user',chat_scope:'all',enabled:true,quote_reply:true,description:'',response:'',flow:[],image_flow:[]};
    previousBodyOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    editorReturnFocus = document.activeElement;
    originalId = command?.id || null;
    document.getElementById('editorTitleText').textContent = command ? 'Edit command' : 'New command';
    document.getElementById('editorSubtitle').textContent = command?.trigger || 'Enter a trigger, action, and response.';
    for (const key of ['name','trigger','description','response']) form.elements[key].value = draft[key] || '';
    form.elements.response_type.value = draft.response_type || 'text';
    form.elements.response_video_source.value = draft.response_video_source || '';
    form.elements.response_image_source.value = draft.response_image_source || '';
    form.elements.processing_delay_seconds.value = draft.processing_delay_seconds ?? (command ? 0 : 3);
    form.elements.aliases.value = (draft.aliases || []).join(', ');
    form.elements.required_params.value = (draft.required_params || []).join(', ');
    form.elements.action.value = draft.action || 'text';
    form.elements.chat_scope.value = draft.chat_scope || (privateOnlyActions.includes(draft.action) ? 'private' : 'all');
    form.elements.enabled.checked = Boolean(draft.enabled);
    form.elements.quote_reply.checked = draft.quote_reply !== false;
    setRole(draft.action); setChatScope(draft.action); setResponseTemplateVisibility(draft.action); drawFlow(); editor.classList.remove('hidden');
    selectEditorTab('settings'); resetPreviewData(); editorBaseline = JSON.stringify(collectCommand()); updateDirtyState(); form.elements.name.focus();
  }
  async function closeEditor(force = false) {
    if (saving || executing) return;
    previewRequest++; clearTimeout(previewTimer);
    if (!force && editorDirty() && !(await themedSwal({icon:'warning',title:t('Discard unsaved changes?', 'Buang perubahan yang belum disimpan?'),showCancelButton:true,confirmButtonText:t('Discard', 'Buang'),cancelButtonText:t('Keep editing', 'Lanjutkan edit')})).isConfirmed) return;
    editor.classList.add('hidden'); document.body.style.overflow = previousBodyOverflow;
    draft = null; originalId = null; editorBaseline = ''; (editorReturnFocus?.isConnected ? editorReturnFocus : document.getElementById('addCommand')).focus();
  }
  async function deleteCommand(command) {
    if (saving) return;
    const isIndonesian = document.documentElement.lang === 'id';
    const result = await themedSwal({
      icon: 'warning',
      title: isIndonesian ? 'Hapus command?' : 'Delete command?',
      text: isIndonesian
        ? `Ketik nama fungsi "${command.name}" untuk mengonfirmasi penghapusan.`
        : `Type the function name "${command.name}" to confirm deletion.`,
      input: 'text',
      inputPlaceholder: command.name,
      inputAttributes: {autocapitalize: 'off', autocorrect: 'off'},
      inputValidator: value => value?.trim() === command.name
        ? undefined
        : (isIndonesian ? 'Nama fungsi tidak cocok.' : 'The function name does not match.'),
      showCancelButton: true,
      confirmButtonText: isIndonesian ? 'Hapus' : 'Delete',
      cancelButtonText: isIndonesian ? 'Batal' : 'Cancel',
      confirmButtonColor: '#dc2626'
    });
    if (!result.isConfirmed || saving) return;
    const previousCommands = structuredClone(commands);
    commands = commands.filter(item => item.id !== command.id);
    try {
      await saveAll();
    } catch (error) {
      commands = previousCommands;
      renderList();
      showToast('error', error.message);
    }
  }
  function renderList() {
    list.replaceChildren();
    emptyState.classList.toggle('hidden', commands.length !== 0);
    const visibleCommands = filteredCommands();
    document.getElementById('commandCount').textContent = `${visibleCommands.length} / ${commands.length} ${t('commands','command')}`;
    document.getElementById('commandNoResults').classList.toggle('hidden', !commands.length || Boolean(visibleCommands.length));
    for (const command of visibleCommands) {
      const row = document.createElement('div');
      row.className = 'ui-card wa-command-card';
      const open = document.createElement('button'); open.type = 'button'; open.className = 'ui-button ui-button-secondary';
      const info = document.createElement('span'); info.className = 'wa-command-info';
      appendIcon(open, 'Edit');
      const title = document.createElement('span'); title.className = 'block truncate font-medium text-gray-900 dark:text-white'; title.textContent = command.name || '(Unnamed)'; title.title = title.textContent;
      const detail = document.createElement('span'); detail.className = 'block truncate font-mono text-xs text-gray-500'; detail.textContent = `${command.trigger || '(no trigger)'} · ${actionLabels[command.action] || command.action}`;
      const access = document.createElement('span'); access.className = 'block text-xs text-gray-500'; access.textContent = `${adminActions.includes(command.action) || command.role === 'admin' ? 'Admin' : 'User'} · ${command.chat_scope === 'private' ? 'Private' : 'Group / Private'}`;
      info.append(detail, access);
      const state = document.createElement('span'); state.className = `ui-badge shrink-0 ${command.enabled ? 'ui-badge-success' : 'ui-badge-neutral'}`; state.textContent = command.enabled ? 'Active' : 'Inactive';
      const heading = document.createElement('div'); heading.className = 'wa-command-heading'; heading.append(title,state);
      open.appendChild(document.createTextNode(t('Edit','Edit'))); open.addEventListener('click', () => openEditor(command));
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'ui-button ui-button-danger inline-flex shrink-0 items-center gap-1'; appendIcon(remove, 'Delete'); remove.appendChild(document.createTextNode('Delete'));
      remove.addEventListener('click', () => deleteCommand(command));
      const actions = document.createElement('div'); actions.className = 'wa-command-actions'; actions.append(open,remove);
      row.append(heading,info,actions); list.appendChild(row);
    }
  }
  async function load() {
    const response = await fetch('/api/admin/wa-bot/commands', {headers:{'Accept':'application/json'}});
    const data = await readApiJson(response, 'Load commands'); if (!response.ok) throw new Error(data.detail || 'Failed to load commands');
    commands = data.commands || []; loaded = true; document.getElementById('addCommand').disabled = false;
    tokenSelect.replaceChildren(new Option('No token (public APIs only)', ''));
    (data.api_tokens || []).forEach(token => tokenSelect.add(new Option(`${token.name} (${token.prefix || 'token'})`, token.id)));
    tokenSelect.value = data.api_token_id || ''; savedTokenId = tokenSelect.value; updateDirtyState();
    renderList();
    await loadOperations().catch(()=>{});
  }
  async function loadOperations() {
    if (monitorLoading) return;
    monitorLoading = true;
    document.getElementById('refreshWaMessages').disabled = true;
    try {
      const response = await fetch('/api/admin/wa-bot/messages?limit=100', {headers:{'Accept':'application/json'}});
      const data = await readApiJson(response, 'Load message monitor');
      if (!response.ok) throw new Error(data.detail || 'Failed to load message monitor');
      messageRows = data.messages || [];
      lastMonitorRefresh = new Date(); renderMessages();
      document.getElementById('messageRefreshState').textContent = `${t('Updated','Diperbarui')} ${lastMonitorRefresh.toLocaleTimeString(BSnapDates.locale())} · ${t('Filters apply to the latest 100 messages','Filter berlaku pada 100 pesan terbaru')}`;
    } catch (error) {
      document.getElementById('messageRefreshState').textContent = `${t('Refresh failed','Pembaruan gagal')}: ${error.message}${lastMonitorRefresh ? ' · '+t('Last updated','Terakhir diperbarui')+' '+lastMonitorRefresh.toLocaleTimeString(BSnapDates.locale()) : ''}`;
      throw error;
    } finally { monitorLoading = false; document.getElementById('refreshWaMessages').disabled = false; }
  }
  window.setInterval(() => { if (!document.hidden && !document.getElementById('messagesPanel').hidden) loadOperations().catch(() => {}); }, 30000);
  document.getElementById('refreshWaMessages').addEventListener('click', () => loadOperations().catch(error => showToast('error',error.message)));
  async function saveAll(saveToken = false) {
    if (!loaded) throw new Error(t('Load commands before saving','Muat command sebelum menyimpan'));
    if (saving) throw new Error(t('A save is already in progress','Penyimpanan sedang berlangsung'));
    const tokenToSave = saveToken ? tokenSelect.value : savedTokenId;
    saving = true; tokenSelect.disabled = true; document.getElementById('saveCommandButton').disabled = true; document.getElementById('saveCommands').disabled = true;
    try {
      const response = await fetch('/api/admin/wa-bot/commands', {method:'PUT',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({commands,api_token_id:tokenToSave})});
      const data = await readApiJson(response, 'Save changes'); if (!response.ok) throw new Error(data.detail || 'Failed to save changes');
      commands = data.commands || commands;
      if (saveToken) savedTokenId = tokenToSave;
      renderList(); updateDirtyState(); showToast('success', t('WhatsApp Bot changes saved.','Perubahan WhatsApp Bot disimpan.'));
    } finally { saving = false; tokenSelect.disabled = false; document.getElementById('saveCommandButton').disabled = false; updateDirtyState(); }
  }
  document.getElementById('addCommand').addEventListener('click', () => openEditor());
  document.getElementById('emptyAddCommand').addEventListener('click', () => openEditor());
  document.getElementById('saveCommands').addEventListener('click', async event => {
    event.currentTarget.disabled = true;
    try { await saveAll(true); } catch (error) { showToast('error',error.message); }
    finally { updateDirtyState(); }
  });
  document.getElementById('closeEditor').addEventListener('click', () => closeEditor());
  form.elements.action.addEventListener('change', () => {
    draft.action = form.elements.action.value; resetPreviewData();
    draft.role = adminActions.includes(draft.action) ? 'admin' : 'user';
    if (draft.action === 'bot_status' && !originalId) {
      if (!form.elements.name.value.trim()) form.elements.name.value = draft.name = 'Bot Status';
      if (!form.elements.trigger.value.trim()) form.elements.trigger.value = draft.trigger = '/botstatus';
      if (!form.elements.description.value.trim()) form.elements.description.value = draft.description = 'Check WhatsApp bot and gateway connectivity';
    }
    setRole(draft.action); setChatScope(draft.action); setResponseTemplateVisibility(draft.action); drawFlow();
  });
  form.querySelector('[data-add-step]').addEventListener('click', () => {
    const activeFlow = draft.action === 'snap' ? (draft.image_flow ||= []) : (draft.flow ||= []);
    if (activeFlow.length >= 8) { showToast('warning','Maximum 8 API nodes per command.'); return; }
    activeFlow.push({name:`data${activeFlow.length + 1}`,path:'',method:'GET',params:{},body:{}}); drawFlow();
  });
  form.querySelectorAll('[data-add-snap-message]').forEach(button => button.addEventListener('click', () => {
    if (!draft) return;
    const key = button.dataset.addSnapMessage;
    draft[key] ||= [];
    if (draft[key].length >= 10) { showToast('warning','Maximum 10 messages per stage.'); return; }
    draft[key].push({text:'',image_source_type:'none',image_source:''});
    drawSnapMessages(key);
  }));
  function addVariableSuggestion(container, expression, value, formatTimestamp = false) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'ui-button ui-button-secondary font-mono text-xs';
    button.textContent = formatTimestamp ? expression + '|datetime' : expression;
    button.title = formatTimestamp ? 'Insert timestamp formatted in the B-Snap timezone' : (typeof value === 'string' ? value : JSON.stringify(value));
    button.addEventListener('click', () => {
      const variable = formatTimestamp ? expression + '|datetime' : expression;
      insertTemplateAtTarget('{' + '{' + variable + '}' + '}');
    });
    container.appendChild(button);
  }
  function insertTemplateAtTarget(snippet) {
    const destination = form.querySelector('[data-variable-target]').value;
    let textarea = form.elements.response;
    if (destination === 'response_video_source' || destination === 'response_image_source') {
      textarea = form.elements[destination];
    } else if (destination !== 'response') {
      const container = form.querySelector(`[data-snap-message-list="${destination}"]`);
      textarea = lastStageTemplateTarget instanceof HTMLTextAreaElement && container.contains(lastStageTemplateTarget)
        ? lastStageTemplateTarget
        : container.querySelector('textarea');
    }
    if (!textarea) {
      showToast('info', 'Add a message in that stage first.');
      return;
    }
    textarea.setRangeText(snippet, textarea.selectionStart, textarea.selectionEnd, 'end');
    textarea.dispatchEvent(new Event('input', {bubbles:true}));
    selectEditorTab('response');
    textarea.focus();
  }
  form.addEventListener('focusin', event => {
    if (event.target instanceof HTMLTextAreaElement && event.target.closest('[data-snap-message-list]')) {
      lastStageTemplateTarget = event.target;
    }
  });
  form.elements.response_type.addEventListener('change', () => setResponseTemplateVisibility(form.elements.action.value));
  const videoVariableTarget = document.createElement('option');
  videoVariableTarget.value = 'response_video_source'; videoVariableTarget.textContent = 'Video source';
  form.querySelector('[data-variable-target]').appendChild(videoVariableTarget);
  const imageVariableTarget = document.createElement('option');
  imageVariableTarget.value = 'response_image_source'; imageVariableTarget.textContent = 'Image source';
  form.querySelector('[data-variable-target]').appendChild(imageVariableTarget);
  function showFlowVariables(steps, parameters = {}) {
    const container = form.querySelector('[data-test-variables]');
    container.replaceChildren();
    let suggestionCount = 0;
    const arrays = [];
    for (const [name, value] of Object.entries(parameters)) {
      addVariableSuggestion(container, `params.${name}`, value);
      suggestionCount += 1;
    }
    for (const [stepName, result] of Object.entries(steps || {})) {
      if (!result || typeof result !== 'object') continue;
      if (suggestionCount >= 100) break;
      addVariableSuggestion(container, `steps.${stepName}.status_code`, result.status_code);
      suggestionCount += 1;
      const visit = (value, path) => {
        if (suggestionCount >= 100) return;
        const expression = `steps.${stepName}.body${path}`;
        addVariableSuggestion(container, expression, value);
        if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}[T ]/.test(value) && !Number.isNaN(Date.parse(value))) {
          addVariableSuggestion(container, expression, value, true);
        }
        suggestionCount += 1;
        if (Array.isArray(value)) {
          arrays.push({expression, value});
          value.forEach((item, index) => visit(item, `${path}.${index}`));
        }
        else if (value && typeof value === 'object') {
          Object.entries(value).filter(([key]) => /^[a-zA-Z0-9_-]+$/.test(key)).forEach(([key, item]) => visit(item, `${path}.${key}`));
        }
      };
      visit(result.body, '');
    }
    const mapper = form.querySelector('[data-array-mapper]');
    const select = form.querySelector('[data-array-select]');
    select.replaceChildren(...arrays.map(({expression}, index) => new Option(expression, String(index))));
    mapper.classList.toggle('hidden', arrays.length === 0);
    mapper._arrayData = arrays;
    drawArrayFields();
  }
  function drawArrayFields() {
    const mapper = form.querySelector('[data-array-mapper]');
    const select = form.querySelector('[data-array-select]');
    const fields = form.querySelector('[data-array-fields]');
    fields.replaceChildren();
    const selected = mapper._arrayData?.[Number(select.value)];
    if (!selected) return;
    const first = selected.value[0];
    const properties = [];
    const collect = (value, prefix = '') => {
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        Object.entries(value).forEach(([key, nested]) => {
          if (!/^[a-zA-Z0-9_-]+$/.test(key)) return;
          collect(nested, prefix ? `${prefix}.${key}` : key);
        });
      } else if (!Array.isArray(value)) {
        properties.push(prefix);
      }
    };
    if (first && typeof first === 'object' && !Array.isArray(first)) collect(first);
    else if (selected.value.length && !Array.isArray(first)) properties.push('');
    properties.slice(0, 30).forEach(property => {
      const label = document.createElement('label');
      label.className = 'inline-flex items-center gap-1.5';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.arrayProperty = property;
      checkbox.addEventListener('change', updateInsertMappingState);
      label.append(checkbox, document.createTextNode(property || 'value'));
      fields.appendChild(label);
    });
    updateInsertMappingState();
  }
  function updateInsertMappingState() {
    const hasSelectedProperty = [...form.querySelectorAll('[data-array-property]')].some(input => input.checked);
    form.querySelector('[data-insert-array]').disabled = !hasSelectedProperty;
  }
  form.querySelector('[data-array-select]').addEventListener('change', drawArrayFields);
  form.querySelector('[data-insert-array]').addEventListener('click', () => {
    const mapper = form.querySelector('[data-array-mapper]');
    const array = mapper._arrayData?.[Number(form.querySelector('[data-array-select]').value)];
    if (!array) return;
    const properties = [...form.querySelectorAll('[data-array-property]:checked')].map(input => input.dataset.arrayProperty);
    if (!properties.length) return;
    const token = (content) => '{' + '{' + content + '}' + '}';
    const includeIndex = form.querySelector('[data-array-index]').checked;
    const values = properties.map(property => token(`this${property ? `.${property}` : ''}`));
    const row = `${includeIndex ? token('@index1') + '. ' : ''}${values.join(' | ')}`;
    const snippet = `${token(`#each ${array.expression}`)}\n${row}\n${token('/each')}`;
    insertTemplateAtTarget(snippet);
  });
  form.querySelector('[data-test-flow]').addEventListener('click', async event => {
    const button = event.currentTarget;
    if (!draft || executing) return;
    const testDraft = collectCommand();
    const sender = form.querySelector('[data-test-sender]').value.trim();
    if (!sender) { showToast('warning',t('Enter a whitelisted sender before executing','Isi nomor pengirim whitelist sebelum menjalankan aksi')); form.querySelector('[data-test-sender]').focus(); return; }
    const flow = testDraft.action === 'api_flow' ? testDraft.flow : testDraft.image_flow;
    const summary = `${actionLabels[testDraft.action] || testDraft.action}\n${t('Sender','Pengirim')}: ${sender}\n${t('Argument','Argumen')}: ${form.querySelector('[data-test-argument]').value}\n${(flow || []).map(step=>`${step.method || 'GET'} ${step.path}`).join('\n')}\n${t('This executes the action using sender permissions. WhatsApp messages are not sent.','Aksi akan dijalankan dengan izin pengirim. Pesan WhatsApp tidak dikirim.')}`;
    if (!(await themedSwal({icon:'warning',title:t('Execute this action?','Jalankan aksi ini?'),text:summary,showCancelButton:true,confirmButtonText:t('Execute action','Jalankan aksi'),cancelButtonText:t('Cancel','Batal'),confirmButtonColor:testDraft.action==='whitelist_remove' ? '#b91c1c' : '#2563eb'})).isConfirmed || !draft) return;
    clearTimeout(previewTimer); previewRequest++;
    executing = true; form.inert = true; document.getElementById('saveCommandButton').disabled = true; updateDirtyState();
    const resultPanel = form.querySelector('[data-test-result]');
    const status = form.querySelector('[data-test-status]');
    const output = form.querySelector('[data-test-json]');
    resultPanel.classList.remove('hidden');
    status.textContent = 'Running API flow…';
    status.className = 'mb-2 text-sm text-gray-500';
    output.textContent = '';
    const responsePreview = form.querySelector('[data-test-response-preview]');
    responsePreview.textContent = ''; responsePreview.classList.add('hidden');
    form.querySelector('[data-test-variables]').replaceChildren();
    button.disabled = true;
    try {
      const isAPIFlow = draft.action === 'api_flow';
      const testCommand = testDraft;
      const response = await fetch(isAPIFlow ? '/api/admin/wa-bot/test-flow' : '/api/admin/wa-bot/test-action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: JSON.stringify({
          command: testCommand,
          execute: true,
          flow: testDraft.action === 'api_flow' ? (testDraft.flow || []) : (testDraft.image_flow || []),
          api_token_id: tokenSelect.value,
          argument: form.querySelector('[data-test-argument]').value,
          sender: form.querySelector('[data-test-sender]').value,
          required_params: form.elements.required_params.value.split(',').map(value => value.trim()).filter(Boolean)
        })
      });
      const data = await readApiJson(response, 'Run API flow test');
      const previewText = response.ok ? data.response_preview : (data.failure_previews || []).map(item => item.text || '').filter(Boolean).join('\n');
      if (previewText) { responsePreview.textContent = previewText; responsePreview.classList.remove('hidden'); }
      if (data.steps) {
        output.textContent = JSON.stringify(data.steps, null, 2);
        showFlowVariables(data.steps, data.params);
      }
      if (data.steps) {
        form.querySelector('[data-preview-context]').value = JSON.stringify(data.steps,null,2);
        form.querySelector('[data-preview-outcome]').value = response.ok ? 'success' : 'failure';
        await renderConversation(data.steps, data.response_preview, response.ok ? 'success' : 'failure', true);
      }
      if (!response.ok) throw new Error(data.detail || 'API flow test failed');
      status.textContent = 'Test succeeded. Click a variable below to add it to the response template.';
      status.className = 'mb-2 text-sm text-green-700 dark:text-green-300';
      output.textContent = JSON.stringify(data.steps, null, 2);
      showFlowVariables(data.steps, data.params);
    } catch (error) {
      status.textContent = error.message;
      status.className = 'mb-2 text-sm text-red-600 dark:text-red-300';
    } finally {
      executing = false; form.inert = false; button.disabled = false; document.getElementById('saveCommandButton').disabled = false; updateDirtyState();
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (!draft || saving || executing) return;
    const duplicates = commandConflicts(collectCommand());
    if (duplicates) { selectEditorTab('settings'); showToast('warning',duplicates); return; }
    draft.name = form.elements.name.value.trim(); draft.trigger = form.elements.trigger.value.trim().toLowerCase();
    draft.description = form.elements.description.value.trim(); draft.response = form.elements.response.value;
    draft.aliases = form.elements.aliases.value.split(',').map(value => value.trim().toLowerCase()).filter(Boolean);
    draft.required_params = form.elements.required_params.value.split(',').map(value => value.trim()).filter(Boolean);
    draft.action = form.elements.action.value; draft.role = adminActions.includes(draft.action) ? 'admin' : 'user'; draft.chat_scope = privateOnlyActions.includes(draft.action) ? 'private' : form.elements.chat_scope.value; draft.enabled = form.elements.enabled.checked; draft.quote_reply = form.elements.quote_reply.checked;
    draft.response_type = form.elements.response_type.value;
    draft.response_video_source = form.elements.response_video_source.value.trim();
    draft.response_image_source = form.elements.response_image_source.value.trim();
    draft.processing_delay_seconds = Number(form.elements.processing_delay_seconds.value);
    if (draft.action === 'api_flow') {
      if (!draft.flow?.length) { showToast('warning','Add at least one API node.'); return; }
      for (const step of draft.flow) {
        if (typeof step.params === 'string') { try { step.params = JSON.parse(step.params || '{}'); } catch { showToast('error',`Query params for node ${step.name} must be valid JSON.`); return; } }
      }
    }
    {
      const stageLists = [draft.processing_messages || [], draft.failure_messages || []];
      if (draft.action === 'snap' && stageLists.some(messages => messages.length < 1 || messages.length > 10)) { showToast('warning','Each snapshot stage needs 1 to 10 messages.'); return; }
      if (stageLists.some(messages => messages.length > 10)) { showToast('warning','Each stage supports up to 10 messages.'); return; }
      if (stageLists.some(messages => messages.some(item =>
        (!item.text?.trim() && item.image_source_type === 'none')
        || (['url','api'].includes(item.image_source_type) && !item.image_source?.trim())
      ))) { showToast('warning','Each message needs text or an image source.'); return; }
      if (stageLists.some(messages => messages.some(item => item.image_source_type === 'api'))
        && !(draft.action === 'snap' ? draft.image_flow?.length : (draft.action === 'api_flow' ? draft.flow?.length : false))) {
        showToast('warning','Add an API flow before using API result image URLs.'); return;
      }
      for (const step of draft.action === 'snap' ? (draft.image_flow || []) : []) {
        if (typeof step.params === 'string') { try { step.params = JSON.parse(step.params || '{}'); } catch { showToast('error',`Query params for node ${step.name} must be valid JSON.`); return; } }
      }
    }
    const previousCommands = structuredClone(commands);
    const index = commands.findIndex(item => item.id === originalId);
    if (index >= 0) commands[index] = draft; else commands.push(draft);
    try {
      await saveAll();
      closeEditor(true);
    } catch (error) {
      commands = previousCommands;
      renderList();
      showToast('error',error.message);
    }
  });
  window.addEventListener('bsnap:languagechange', () => {
    renderList();
    updateDirtyState(); renderMessages();
    if (draft) {
      Object.assign(draft, collectCommand());
      document.getElementById('editorTitleText').textContent = originalId ? 'Edit command' : 'New command';
      document.getElementById('editorSubtitle').textContent = draft.trigger || 'Enter a trigger, action, and response.';
      setRole(draft.action);
      setChatScope(draft.action);
      setResponseTemplateVisibility(draft.action);
      drawFlow(); scheduleConversationPreview();
    }
  });
  document.getElementById('addCommand').disabled = true; updateDirtyState();
  list.textContent = t('Loading commands?','Memuat command?');
  load().catch(error => { list.textContent = error.message; });
})();
