/* ---------------------------------------------------------------------------
 * viac_capture.js  --  one-paste export of your VIAC data
 *
 * Paste this whole file into the DevTools console of https://app.viac.ch while
 * you are logged in.  It never sees your password and nothing leaves the browser
 * tab: it watches the requests the VIAC web app already makes, and then replays
 * the "download document" request once per buy/sell transaction.
 *
 * Result: a single viac_export.zip containing
 *     transactions.json
 *     pdfs/<documentNumber>.pdf   (one per buy/sell)
 * which viac_to_pp.py reads directly:   py viac_to_pp.py viac_export.zip
 *
 * Usage:
 *   1. Log into https://app.viac.ch
 *   2. Paste this file into the console and press Enter.
 *      (Chrome/Edge may ask you to type  allow pasting  first.)
 *   3. Open one of your portfolios.
 *
 * That is the whole procedure. The transactions request the app makes when you
 * open a portfolio carries an account number and headers that cannot be
 * reconstructed from outside, so the app is left to make it; everything after
 * that - the document URL, all the PDFs, the zip - happens by itself.
 *
 *   viac.status()     shows what it is waiting for
 *   viac.run()        start (or restart) the download by hand
 *   viac.portfolios() lists the portfolios captured so far
 *   viac.saveJson()   downloads only transactions.json
 *   viac.debug()      dumps what was learned (for bug reports)
 * ------------------------------------------------------------------------- */
(function () {
  'use strict';

  if (window.__viacCaptureInstalled) {
    console.log('%c[viac] already installed - type viac.status()', 'color:#0a0');
    return;
  }
  window.__viacCaptureInstalled = true;

  var log  = function (m) { console.log('%c[viac] ' + m, 'color:#06c'); };
  var ok   = function (m) { console.log('%c[viac] ' + m, 'color:#0a0;font-weight:bold'); };
  var warn = function (m) { console.log('%c[viac] ' + m, 'color:#c60;font-weight:bold'); };
  var err  = function (m) { console.log('%c[viac] ' + m, 'color:#c00;font-weight:bold'); };

  // ---------------------------------------------------------------- state ---
  var state = {
    payload: null,        // the parsed {"transactions": {...}} object
    payloadUrl: null,
    pdfSample: null,      // {method, url, headers, body}
    authHeaders: {},      // headers worth replaying (bearer token, csrf, ...)
    docKey: null,         // which transaction field the document URL is built from
    docValue: null,       // that field's value in the sample request
    template: null,       // sample URL with the document number replaced by {DOC}
    txHeaders: null,      // headers the app itself used for the transactions call
    txUrls: []            // every transactions URL that returned real data
  };

  /* The API refuses a bare request with 403 and an HTML error page. Measured on
   * app.viac.ch: Accept alone is refused, X-Requested-With is refused, and the
   * app's own header set is accepted - the one that matters is X-Same-Domain.
   * None of these is a secret; they are constants the app sends on every call.
   * Only used to repeat a request we have already seen, when its own headers are
   * somehow unavailable. */
  var DEFAULT_API_HEADERS = {
    'Accept': 'application/json',
    'Cache-Control': 'no-cache, no-store',
    'Pragma': 'no-cache',
    'Expires': '0',
    'X-Same-Domain': '1'
  };

  var AUTH_HEADERS = ['authorization', 'x-xsrf-token', 'x-csrf-token',
                      'x-auth-token', 'x-api-key', 'x-requested-with',
                      'accept-language'];
  var HEADER_BLOCKLIST = ['host', 'connection', 'content-length', 'cookie',
                          'origin', 'referer', 'user-agent', 'accept-encoding'];

  function rememberAuth(headers) {
    Object.keys(headers || {}).forEach(function (k) {
      if (AUTH_HEADERS.indexOf(k.toLowerCase()) !== -1 && headers[k]) {
        state.authHeaders[k] = headers[k];
      }
    });
  }

  function replayHeaders(extra) {
    var h = {};
    Object.keys(state.authHeaders).forEach(function (k) { h[k] = state.authHeaders[k]; });
    Object.keys(extra || {}).forEach(function (k) {
      if (HEADER_BLOCKLIST.indexOf(k.toLowerCase()) === -1) h[k] = extra[k];
    });
    return h;
  }

  // ------------------------------------------------------------ detection ---
  function looksLikeTransactionList(v) {
    return Array.isArray(v) && v.length > 0 && v[0] && typeof v[0] === 'object' &&
           'type' in v[0] && ('valueDate' in v[0] || 'bookingDate' in v[0]);
  }

  /* Portfolios are only ever added, never replaced by a later response that
   * happens to contain fewer of them. VIAC answers with every portfolio at once,
   * but this way clicking through the app can only ever complete the picture. */
  function merge(byPortfolio, url) {
    if (!state.payload) state.payload = { transactions: {} };
    var changed = false;
    Object.keys(byPortfolio).forEach(function (id) {
      var incoming = byPortfolio[id] || [];
      var have = state.payload.transactions[id];
      if (!have || incoming.length >= have.length) {
        if (!have || incoming.length !== have.length) changed = true;
        state.payload.transactions[id] = incoming;
      }
    });
    state.payloadUrl = url;
    if (state.txUrls.indexOf(url) === -1) state.txUrls.push(url);
    if (changed) {
      ok('have ' + countTx() + ' transactions across ' +
         Object.keys(state.payload.transactions).length + ' portfolio(s): ' +
         Object.keys(state.payload.transactions).join(', '));
    }
    return changed;
  }

  function considerJson(url, obj, headers) {
    if (!obj || typeof obj !== 'object') return;

    // Primary shape: {"transactions": {"<portfolioId>": [ ... ]}}
    if (obj.transactions && typeof obj.transactions === 'object' &&
        !Array.isArray(obj.transactions)) {
      var keys = Object.keys(obj.transactions);
      if (keys.length && looksLikeTransactionList(obj.transactions[keys[0]])) {
        if (headers && Object.keys(headers).length) state.txHeaders = headers;
        merge(obj.transactions, url);
        return;
      }
    }

    // Fallback shape: a bare array of transactions for a single portfolio.
    if (looksLikeTransactionList(obj)) {
      var id = (url.match(/(\d{4,})/g) || ['portfolio']).pop();
      if (headers && Object.keys(headers).length) state.txHeaders = headers;
      var one = {};
      one[id] = obj;
      merge(one, url);
    }
  }

  function considerPdf(method, url, headers, body, contentType) {
    if (state.pdfSample) return;                       // the first one is enough
    var isPdf = (contentType || '').indexOf('pdf') !== -1 ||
                /\.pdf(\?|$)/i.test(url) ||
                /document|beleg|receipt/i.test(url);
    if (!isPdf) return;
    state.pdfSample = { method: method, url: url, headers: headers || {}, body: body };
    log('saw a document request: ' + method + ' ' + url);
    learnTemplate();
  }

  // ------------------------------------------------------------- the hooks ---
  var origFetch = window.fetch;
  window.fetch = function (input, init) {
    var url, method, headers = {}, body = null;
    try {
      url = (typeof input === 'string') ? input : input.url;
      url = new URL(url, location.href).href;
      method = ((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if (init && init.headers) {
        new Headers(init.headers).forEach(function (v, k) { headers[k] = v; });
      } else if (input && input.headers && input.headers.forEach) {
        input.headers.forEach(function (v, k) { headers[k] = v; });
      }
      if (init && typeof init.body === 'string') body = init.body;
      rememberAuth(headers);
    } catch (e) { /* never break the app */ }

    var p = origFetch.apply(this, arguments);
    try {
      p.then(function (res) {
        try {
          var ct = res.headers.get('content-type') || '';
          considerPdf(method, url, headers, body, ct);
          if (ct.indexOf('json') !== -1) {
            res.clone().json().then(function (o) { considerJson(url, o, headers); },
                                    function () {});
          }
        } catch (e) {}
        return res;
      }, function () {});
    } catch (e) {}
    return p;
  };

  var xopen = XMLHttpRequest.prototype.open;
  var xsend = XMLHttpRequest.prototype.send;
  var xhdr  = XMLHttpRequest.prototype.setRequestHeader;

  XMLHttpRequest.prototype.open = function (method, url) {
    this.__viac = { method: (method || 'GET').toUpperCase(), url: url, headers: {} };
    return xopen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) {
    if (this.__viac) this.__viac.headers[k] = v;
    return xhdr.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function (body) {
    var self = this;
    if (self.__viac) {
      if (typeof body === 'string') self.__viac.body = body;
      rememberAuth(self.__viac.headers);
      self.addEventListener('load', function () {
        try {
          var ct = self.getResponseHeader('content-type') || '';
          var abs = new URL(self.__viac.url, location.href).href;
          considerPdf(self.__viac.method, abs, self.__viac.headers, self.__viac.body, ct);
          if (ct.indexOf('json') !== -1) {
            if (self.responseType === 'json') {
              considerJson(abs, self.response, self.__viac.headers);
            } else if (self.responseType === '' || self.responseType === 'text') {
              considerJson(abs, JSON.parse(self.responseText), self.__viac.headers);
            }
          }
        } catch (e) {}
      });
    }
    return xsend.apply(this, arguments);
  };

  function refetch(url) {
    var headers = replayHeaders(state.txHeaders || DEFAULT_API_HEADERS);
    return origFetch.call(window, url, {
      credentials: 'include',
      headers: headers
    }).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (obj) {
      if (obj) considerJson(url, obj, state.txHeaders);
    }).catch(function () { /* refused, not JSON, expired - just move on */ });
  }

  /* A PDF opened through a plain link or window.open never goes through fetch or
   * XHR, but it does show up in the resource timing buffer. Last resort only:
   * the document URL is normally known without any of this. */
  function scanPerformance() {
    if (state.pdfSample || state.template) return;
    var entries = performance.getEntriesByType('resource') || [];
    for (var i = entries.length - 1; i >= 0; i--) {
      var n = entries[i].name;
      if (/\.pdf(\?|$)/i.test(n) || /\/files\/document\//i.test(n)) {
        state.pdfSample = { method: 'GET', url: n, headers: {}, body: null };
        log('found a document URL in the resource timeline: ' + n);
        learnTemplate();
        return;
      }
    }
  }

  // --------------------------------------------------------- the template ---
  function trades() {
    var out = [];
    var t = state.payload && state.payload.transactions;
    if (!t) return out;
    Object.keys(t).forEach(function (acc) {
      (t[acc] || []).forEach(function (tx) {
        if (tx.type === 'TRADE_BUY' || tx.type === 'TRADE_SELL') out.push(tx);
      });
    });
    return out;
  }

  function countTx() {
    var t = state.payload && state.payload.transactions, n = 0;
    if (!t) return 0;
    Object.keys(t).forEach(function (a) { n += (t[a] || []).length; });
    return n;
  }

  /* Work out which field of a transaction the document URL is built from, by
   * looking for field values inside the one URL we observed. A field matching
   * exactly one transaction is the document id; a field matching many is
   * something constant like the portfolio id, so it is ignored. */
  function learnTemplate() {
    if (!state.pdfSample || !state.payload) return;
    var url = state.pdfSample.url;
    var body = typeof state.pdfSample.body === 'string' ? state.pdfSample.body : '';
    var hay = url + '\n' + body;
    var hits = {}, values = {};

    trades().forEach(function (tx) {
      Object.keys(tx).forEach(function (k) {
        var v = tx[k];
        if (v === null || v === undefined || typeof v === 'object') return;
        var sv = String(v);
        if (sv.length < 4) return;
        if (hay.indexOf(sv) !== -1) {
          hits[k] = (hits[k] || 0) + 1;
          values[k] = sv;
        }
      });
    });

    var best = null;
    Object.keys(hits).forEach(function (k) {
      if (hits[k] !== 1) return;                       // constant -> not a document id
      if (best === null || k === 'documentNumber') best = k;
    });
    if (best === null) {
      warn('could not tell which transaction field the document URL uses. ' +
           'Run viac.debug() and open an issue with the (anonymised) output.');
      return;
    }

    state.docKey = best;
    state.docValue = values[best];
    state.template = url.split(values[best]).join('{DOC}');
    if (state.template === url) {
      // The id is only in the request body, not the URL.
      state.template = url;
    }
    ok('learned the document URL: ' + state.template + '   (field: ' + best + ')');
    announceReady();
  }

  function ready() { return !!(state.payload && state.template); }

  var announced = false;
  function announceReady() {
    if (announced || !ready()) return false;
    announced = true;
    ok('got everything it needs - starting the download');
    download_all();
    return true;
  }

  /* ---------------------------------------------------------- autodiscovery --
   * Endpoints VIAC is known to use. They are tried first, so in the normal case
   * nothing has to be clicked at all. Each one is verified before it is trusted -
   * a transactions URL has to return a transactions payload, and a document URL
   * has to return something starting with %PDF - so a stale entry here can only
   * cost one failed request, never produce a wrong export.
   * Paths are relative to the page's own origin. */
  var KNOWN_DOC_PATHS = [
    '/files/document/{DOC}'          // confirmed on app.viac.ch
  ];

  function firstDocumentNumber() {
    var t = trades();
    for (var i = 0; i < t.length; i++) {
      if (t[i].documentNumber) return String(t[i].documentNumber);
    }
    return null;
  }

  /* Try each known document path against a real document number. Only a response
   * that actually is a PDF counts as proof. */
  function probeDocument() {
    if (state.template) return Promise.resolve(true);
    var docNo = firstDocumentNumber();
    if (!docNo) return Promise.resolve(false);

    var i = 0;
    function next() {
      if (i >= KNOWN_DOC_PATHS.length) return false;
      var template = location.origin + KNOWN_DOC_PATHS[i++];
      var url = template.split('{DOC}').join(encodeURIComponent(docNo));
      return origFetch.call(window, url, {
        credentials: 'include',
        headers: replayHeaders({})
      }).then(function (res) {
        return res.ok ? res.arrayBuffer() : null;
      }).then(function (buf) {
        if (buf) {
          var u8 = new Uint8Array(buf);
          if (u8.length > 4 && String.fromCharCode(u8[0], u8[1], u8[2], u8[3]) === '%PDF') {
            state.pdfSample = { method: 'GET', url: url, headers: {}, body: null };
            state.docKey = 'documentNumber';
            state.docValue = docNo;
            state.template = template;
            ok('document URL: ' + template);
            return true;
          }
        }
        return next();
      }).catch(function () { return next(); });
    }
    return Promise.resolve(next());
  }

  /* The transactions URL contains an account number that is not served anywhere
   * we can reach, and the endpoint refuses requests we build ourselves. So the
   * app is asked to make that one request the normal way - by you opening a
   * portfolio - and everything after that is automatic. */
  function askForPdfClick() {
    warn('could not work out the document URL by itself.');
    console.log('   Please open Transactions and click ONE buy or sell entry.');
    console.log('   Nothing else to do - it keeps watching and starts by itself.');
  }

  // -------------------------------------------------------------- zip file ---
  var CRC = (function () {
    var t = new Uint32Array(256);
    for (var i = 0; i < 256; i++) {
      var c = i;
      for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      t[i] = c >>> 0;
    }
    return t;
  })();

  function crc32(u8) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  // Minimal ZIP writer, "stored" (no compression) - PDFs are compressed already.
  function makeZip(files) {
    var enc = new TextEncoder();
    var now = new Date();
    var time = ((now.getHours() & 31) << 11) | ((now.getMinutes() & 63) << 5) |
               (Math.floor(now.getSeconds() / 2) & 31);
    var date = (((now.getFullYear() - 1980) & 127) << 9) |
               (((now.getMonth() + 1) & 15) << 5) | (now.getDate() & 31);

    var local = [], central = [], offset = 0;

    files.forEach(function (f) {
      var name = enc.encode(f.name);
      var crc = crc32(f.data);

      var lh = new Uint8Array(30 + name.length), lv = new DataView(lh.buffer);
      lv.setUint32(0, 0x04034b50, true); lv.setUint16(4, 20, true);
      lv.setUint16(6, 0x0800, true);     lv.setUint16(8, 0, true);
      lv.setUint16(10, time, true);      lv.setUint16(12, date, true);
      lv.setUint32(14, crc, true);
      lv.setUint32(18, f.data.length, true); lv.setUint32(22, f.data.length, true);
      lv.setUint16(26, name.length, true);   lv.setUint16(28, 0, true);
      lh.set(name, 30);

      var ch = new Uint8Array(46 + name.length), cv = new DataView(ch.buffer);
      cv.setUint32(0, 0x02014b50, true); cv.setUint16(4, 20, true);
      cv.setUint16(6, 20, true);         cv.setUint16(8, 0x0800, true);
      cv.setUint16(10, 0, true);         cv.setUint16(12, time, true);
      cv.setUint16(14, date, true);      cv.setUint32(16, crc, true);
      cv.setUint32(20, f.data.length, true); cv.setUint32(24, f.data.length, true);
      cv.setUint16(28, name.length, true);
      cv.setUint32(42, offset, true);
      ch.set(name, 46);

      local.push(lh, f.data);
      central.push(ch);
      offset += lh.length + f.data.length;
    });

    var cdSize = central.reduce(function (a, b) { return a + b.length; }, 0);
    var eocd = new Uint8Array(22), ev = new DataView(eocd.buffer);
    ev.setUint32(0, 0x06054b50, true);
    ev.setUint16(8, files.length, true); ev.setUint16(10, files.length, true);
    ev.setUint32(12, cdSize, true);      ev.setUint32(16, offset, true);

    return new Blob(local.concat(central, [eocd]), { type: 'application/zip' });
  }

  function download(blob, name) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 5000);
  }

  // ------------------------------------------------------------------ run ---
  function fetchDoc(docValue) {
    var s = state.pdfSample;
    var url = state.template.split('{DOC}').join(encodeURIComponent(docValue));
    var init = {
      method: s.method,
      credentials: 'include',
      headers: replayHeaders(s.headers)
    };
    if (s.method !== 'GET' && s.method !== 'HEAD' && typeof s.body === 'string') {
      init.body = state.docValue ? s.body.split(state.docValue).join(docValue) : s.body;
    }
    return origFetch.call(window, url, init).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.arrayBuffer();
    }).then(function (buf) {
      var u8 = new Uint8Array(buf);
      if (u8.length < 5 || String.fromCharCode(u8[0], u8[1], u8[2], u8[3]) !== '%PDF') {
        throw new Error('response was not a PDF (' + u8.length + ' bytes)');
      }
      return u8;
    });
  }

  function run(concurrency) {
    if (!state.payload)  { err('no transactions captured yet - see viac.status()'); return; }
    if (!state.template) { err('no document URL learned yet - see viac.status()'); return; }
    // Ask each endpoint once more so the zip holds everything they currently
    // return, whatever happened to be open when the app last called them.
    var urls = state.txUrls.slice();
    var i = 0;
    function next() {
      if (i >= urls.length) return Promise.resolve();
      return refetch(urls[i++]).then(next);
    }
    return next().then(function () { return download_all(concurrency); });
  }

  function download_all(concurrency) {
    if (state.running) { warn('a download is already running'); return Promise.resolve(); }
    state.running = true;
    var wanted = [], seen = {};
    trades().forEach(function (tx) {
      var v = tx[state.docKey];
      if (v === null || v === undefined) return;
      v = String(v);
      if (!seen[v]) { seen[v] = 1; wanted.push(v); }
    });

    log('downloading ' + wanted.length + ' documents ...');
    var files = [], failed = [], done = 0, i = 0;
    var N = concurrency || 4;

    function next() {
      if (i >= wanted.length) return Promise.resolve();
      var doc = wanted[i++];
      return fetchDoc(doc).then(function (u8) {
        files.push({
          name: 'pdfs/' + doc.replace(/[^A-Za-z0-9._-]/g, '_') + '.pdf',
          data: u8
        });
      }).catch(function (e) {
        failed.push(doc + ': ' + e.message);
      }).then(function () {
        done++;
        if (done % 25 === 0 || done === wanted.length) {
          log(done + '/' + wanted.length + ' documents');
        }
        return next();
      });
    }

    var workers = [];
    for (var w = 0; w < N; w++) workers.push(next());

    return Promise.all(workers).then(function () {
      var json = JSON.stringify(state.payload, null, 2);
      files.unshift({ name: 'transactions.json', data: new TextEncoder().encode(json) });
      download(makeZip(files), 'viac_export.zip');
      ok('viac_export.zip downloaded: transactions.json + ' + (files.length - 1) + ' PDFs');
      printPortfolios('in the zip');
      log('now run:   py viac_to_pp.py viac_export.zip');
      if (failed.length) {
        warn(failed.length + ' document(s) could not be downloaded:');
        failed.forEach(function (f) { console.log('   ' + f); });
        warn('viac_to_pp.py will list them again; you can add those PDFs by hand.');
      }
      state.running = false;
    }, function (e) {
      state.running = false;
      throw e;
    });
  }

  /* Printed so you can check nothing is missing: if a portfolio of yours is not
   * listed, open it in the app and run again - portfolios are only ever added. */
  function printPortfolios(where) {
    var t = state.payload && state.payload.transactions;
    if (!t) return;
    var ids = Object.keys(t);
    log(ids.length + ' portfolio(s) ' + (where || 'captured') + ':');
    ids.forEach(function (id) {
      var list = t[id] || [];
      var n = list.filter(function (tx) {
        return tx.type === 'TRADE_BUY' || tx.type === 'TRADE_SELL';
      }).length;
      console.log('   ' + id + ': ' + list.length + ' transactions, ' + n + ' buy/sell');
    });
  }

  function status() {
    console.log('%c[viac] status', 'font-weight:bold');
    console.log('  transactions : ' + (state.payload
      ? countTx() + ' in ' + Object.keys(state.payload.transactions).length + ' portfolio(s)'
      : 'MISSING - open one of your portfolios in the app'));
    console.log('  document URL : ' + (state.template
      ? state.template + '  (field: ' + state.docKey + ')'
      : 'MISSING - open Transactions and click ONE buy or sell entry'));
    console.log('  buy/sell     : ' + trades().length);
    printPortfolios();
    if (ready()) ok('ready - type   viac.run()');
  }

  window.viac = {
    run: run,
    status: status,
    portfolios: printPortfolios,
    saveJson: function () {
      if (!state.payload) { err('nothing captured yet'); return; }
      download(new Blob([JSON.stringify(state.payload, null, 2)],
                        { type: 'application/json' }), 'transactions.json');
    },
    debug: function () {
      return {
        payloadUrl: state.payloadUrl,
        pdfSample: state.pdfSample,
        docKey: state.docKey,
        template: state.template,
        sampleTrade: trades()[0] || null
      };
    }
  };

  // Watch what the app does. As soon as the transactions come past, work out the
  // document URL and start downloading - without anything else being typed.
  var ticks = 0;
  var probedDocument = false;
  var timer = setInterval(function () {
    if (state.running) return;
    if (state.payload && !state.template && !probedDocument) {
      probedDocument = true;
      probeDocument().then(function () {
        if (state.template) return;
        scanPerformance();
        if (!state.template && !state.pdfSample) askForPdfClick();
      });
    }
    if (state.payload && state.pdfSample && !state.template) learnTemplate();
    if (announceReady() || ++ticks > 900) clearInterval(timer);   // give up after 15 min
  }, 1000);

  console.log('%c[viac] ready. Now open one of your portfolios in the app.',
              'color:#0a0;font-weight:bold');
  console.log('  Everything after that happens by itself: the transactions are picked up,');
  console.log('  every buy/sell PDF is fetched, and viac_export.zip is downloaded.');
  console.log('  (viac.status() shows what it is still waiting for)');
})();
// A value for the console to echo, so pasting does not just print "undefined".
'viac capture ready - see the lines above';
