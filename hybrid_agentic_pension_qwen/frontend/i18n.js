/* Presentation-only localization: never change form values or API identifiers. */
(() => {
  'use strict';
  const supported = ['ko', 'en', 'zh'];
  const requested = new URLSearchParams(location.search).get('lang');
  let saved;
  try { saved = localStorage.getItem('kkaeum.language'); } catch (_) {}
  let language = supported.includes(requested) ? requested : supported.includes(saved) ? saved : 'ko';
  const rows = window.KKAEUM_TRANSLATIONS;
  const exact = new Map(rows.filter(row => !row[0].includes('{0}')).map(row => [row[0], row]));
  const escapeRE = value => value.replace(/[.*+?^$()|[\]\\]/g, '\\$&');
  const patterns = rows.filter(row => row[0].includes('{0}')).map(row => {
    const tokens = row[0].split(/(\{\d+\})/);
    const numeric = /^\{0\}(년|세|원|분|초)/.test(row[0]);
    return {row, regex:new RegExp('^' + tokens.map(token =>
      /^\{\d+\}$/.test(token) ? (numeric ? '([-+\\d.,]+)' : '(.+?)') : escapeRE(token)).join('') + '$')};
  }).sort((a,b) => b.row[0].length - a.row[0].length);
  const fragments = [...exact.keys()].filter(key => key.length > 2).sort((a,b) => b.length - a.length);
  const fragmentRE = new RegExp(fragments.map(escapeRE).join('|'), 'g');
  const column = () => language === 'en' ? 1 : 2;
  let officialNamesRE = null;
  function registerNames(names) {
    const official = [...new Set(names.filter(Boolean))].sort((a,b)=>b.length-a.length);
    officialNamesRE = official.length ? new RegExp(official.map(escapeRE).join('|'), 'g') : null;
  }
  function text(value, depth = 0) {
    const source = String(value ?? '');
    if(language === 'ko' || !/[가-힣]/.test(source)) return source;
    if(depth === 0 && officialNamesRE) {
      const names=[];
      const protectedSource=source.replace(officialNamesRE, name=>{
        names.push(name); return '\uE000'+(names.length-1)+'\uE001';
      });
      if(names.length) return text(protectedSource,1).replace(/\uE000(\d+)\uE001/g,(_,index)=>names[Number(index)]);
    }
    const normalized = source.replace(/\s+/g, ' ').trim();
    const padding = output => (source.match(/^\s*/)[0] + output + source.match(/\s*$/)[0]);
    if(exact.has(normalized)) return padding(exact.get(normalized)[column()]);
    if(depth < 5) {
      for(const {row,regex} of patterns) {
        const match = normalized.match(regex);
        if(match) return padding(row[column()].replace(/\{(\d+)\}/g, (_,index) => text(match[Number(index)+1], depth+1)));
      }
    }
    // Composite labels (metadata, chart legends) consist of independently translated phrases.
    return source.replace(fragmentRE, key => exact.get(key)[column()]);
  }
  const originals = new WeakMap();
  const attributes = new WeakMap();
  const ignored = 'script,style,[data-no-translate],.chat-msg.user,.chat-snippet,#currentProduct,#sourcePdfName,#compareProviderLabel';
  function skip(element) {
    if(!element || element.closest(ignored)) return true;
    // Product and provider values are official Korean identifiers.
    return element.tagName === 'OPTION' && element.parentElement?.id !== 'investmentType';
  }
  function translateNode(node) {
    if(node.nodeType === Node.TEXT_NODE) {
      if(skip(node.parentElement)) return;
      const last = originals.get(node);
      const source = last && node.data === last.output ? last.source : node.data;
      const output = text(source);
      originals.set(node, {source, output});
      if(node.data !== output) node.data = output;
    } else if(node.nodeType === Node.ELEMENT_NODE) {
      if(skip(node)) return;
      let saved = attributes.get(node);
      if(!saved) { saved={}; attributes.set(node,saved); }
      for(const attr of ['placeholder','title','aria-label']) {
        if(!node.hasAttribute(attr)) continue;
        const current = node.getAttribute(attr);
        const last = saved[attr];
        const source = last && current === last.output ? last.source : current;
        const output = text(source);
        saved[attr] = {source, output};
        if(current !== output) node.setAttribute(attr, output);
      }
      node.childNodes.forEach(translateNode);
    }
  }
  const observer = new MutationObserver(records => {
    observer.disconnect();
    for(const record of records) {
      if(record.type === 'childList') record.addedNodes.forEach(translateNode);
      else translateNode(record.target);
    }
    observe();
  });
  function observe() {
    observer.observe(document.body, {subtree:true,childList:true,characterData:true,attributes:true,
      attributeFilter:['placeholder','title','aria-label']});
  }
  function refresh() {
    observer.disconnect();
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : language;
    document.documentElement.dataset.language = language;
    document.title = language === 'ko' ? '깨움 KKAEUM' : 'KKAEUM';
    document.getElementById('languageSelect').value = language;
    translateNode(document.body);
    observe();
  }
  window.I18n = {text, refresh, registerNames, get language(){return language;},
    get locale(){return {ko:'ko-KR',en:'en-US',zh:'zh-CN'}[language];}};
  document.getElementById('languageSelect').addEventListener('change', event => {
    language = supported.includes(event.target.value) ? event.target.value : 'ko';
    try { localStorage.setItem('kkaeum.language', language); } catch (_) {}
    refresh();
    window.dispatchEvent(new CustomEvent('localechange', {detail:{language}}));
  });
  window.addEventListener('beforeprint', refresh);
  refresh();
})();
