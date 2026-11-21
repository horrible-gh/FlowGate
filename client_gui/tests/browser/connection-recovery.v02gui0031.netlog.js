/* the browser's per-request log for the connection-recovery scenarios.
 * A classic script, loaded before the fixture module, so it sees every request the product makes (the shared axios
 * client and the refresh call both use XMLHttpRequest; fetch is wrapped too) and every EventSource connection attempt.
 * One entry per request (or per stream attempt): wall-clock start / end (Date.now(), the clock the scenario script and
 * the fake server use), method, the path with the query reduced to known keys (any other value, e.g. the stream's
 * token, becomes `*`), the HTTP status or the failure kind (offline / network / timeout / abort; for a stream: open,
 * error_closed, error_retrying, closed_by_client), and the duration. No header, no body, no token is ever kept.
 * The scenario script drains it (window.__netlog.drain) into its report; only requests to the FlowGate API are kept.
 */
(function () {
  'use strict'
  var log = []
  var inflight = new Map()
  var seq = 0
  // Query values that say which object was read; every other value is masked.
  var KEEP = { group_id: 1, project_id: 1, flow_id: 1, after_seq: 1, before_seq: 1, limit: 1, include_terminal: 1,
    force: 1, offset: 1, view: 1 }

  function parse(raw) {
    try {
      var url = new URL(String(raw), location.href)
      if (url.pathname.indexOf('/flowgate') !== 0) return null // the page's own module loads are not API requests
      var query = []
      url.searchParams.forEach(function (value, key) {
        query.push(key + '=' + (KEEP[key] ? encodeURIComponent(value) : '*'))
      })
      return url.pathname.slice('/flowgate'.length) + (query.length ? '?' + query.join('&') : '')
    } catch (e) {
      return null
    }
  }

  function begin(via, method, path) {
    var entry = { id: ++seq, via: via, method: method, path: path, start: Date.now(), end: null, ms: null,
      status: null, error: null }
    inflight.set(entry.id, entry)
    return entry
  }

  function finish(entry, status, error) {
    if (!inflight.has(entry.id)) return
    inflight.delete(entry.id)
    entry.end = Date.now()
    entry.ms = entry.end - entry.start
    entry.status = status
    entry.error = error
    log.push(entry)
  }

  // XMLHttpRequest (axios's browser adapter, the product client and its refresh call).
  var open = XMLHttpRequest.prototype.open
  var send = XMLHttpRequest.prototype.send
  XMLHttpRequest.prototype.open = function (method, url) {
    this.__fgNet = { method: String(method || 'GET').toUpperCase(), path: parse(url) }
    return open.apply(this, arguments)
  }
  XMLHttpRequest.prototype.send = function () {
    var xhr = this
    var meta = xhr.__fgNet
    if (meta && meta.path !== null) {
      var entry = begin('xhr', meta.method, meta.path)
      var failure = null
      xhr.addEventListener('timeout', function () { failure = 'timeout' })
      xhr.addEventListener('abort', function () { failure = failure || 'abort' })
      // A request the browser could not send: `offline` while the browser is offline, else `network`.
      xhr.addEventListener('error', function () { failure = failure || (navigator.onLine ? 'network' : 'offline') })
      xhr.addEventListener('loadend', function () {
        finish(entry, failure ? null : xhr.status, failure)
      })
    }
    return send.apply(this, arguments)
  }

  // fetch (not used by the product client today; logged the same way if it ever is).
  if (typeof window.fetch === 'function') {
    var nativeFetch = window.fetch
    window.fetch = function (input, init) {
      var method = (init && init.method) || (input && input.method) || 'GET'
      var path = parse(typeof input === 'string' || input instanceof URL ? input : input && input.url)
      if (path === null) return nativeFetch.apply(this, arguments)
      var entry = begin('fetch', String(method).toUpperCase(), path)
      return nativeFetch.apply(this, arguments).then(function (response) {
        finish(entry, response.status, null)
        return response
      }, function (error) {
        finish(entry, null, error && error.name === 'AbortError' ? 'abort' : (navigator.onLine ? 'network' : 'offline'))
        throw error
      })
    }
  }

  // EventSource: one entry per connection attempt (until it opens or fails) and per open period (until it drops or
  // the hub closes it). error_closed = the browser gave up (a refused answer: the hub reconnects by itself);
  // error_retrying = the browser retries the same URL.
  var NativeEventSource = window.EventSource
  if (typeof NativeEventSource === 'function') {
    var streams = 0
    window.EventSource = class extends NativeEventSource {
      constructor(url, init) {
        super(url, init)
        var self = this
        var path = parse(url)
        if (path === null) return
        var stream = ++streams
        var current = null
        var start = function () {
          current = begin('eventsource', 'GET', path)
          current.stream = stream
        }
        start()
        this.__fgEnd = function (status, error) {
          if (!current) return
          finish(current, status, error)
          current = null
        }
        this.addEventListener('open', function () {
          self.__fgEnd(200, null)
          start() // the open period
          current.phase = 'open'
        })
        this.addEventListener('error', function () {
          var wasOpen = current && current.phase === 'open'
          var closed = self.readyState === 2
          self.__fgEnd(null, wasOpen ? (closed ? 'dropped_closed' : 'dropped_retrying') : (closed ? 'error_closed' : 'error_retrying'))
          if (!closed) start()
        })
      }

      close() {
        if (this.__fgEnd && this.readyState !== 2) this.__fgEnd(null, 'closed_by_client')
        return super.close()
      }
    }
  }

  window.__netlog = {
    /** Hands over the finished entries; with `cut` (a label: before a reload, at the end) the unfinished ones too. */
    drain: function (cut) {
      var out = log
      log = []
      if (cut) {
        inflight.forEach(function (entry) {
          out.push(Object.assign({}, entry, { end: null, ms: Date.now() - entry.start, error: String(cut) }))
        })
        inflight.clear()
      }
      return out
    },
  }
})()
