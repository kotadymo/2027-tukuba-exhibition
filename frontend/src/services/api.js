const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

function errorMessage(payload, fallback) {
  const detail = payload?.detail
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg || String(item)).join(' / ')
  }
  if (typeof detail === 'string') {
    return detail
  }
  return fallback
}

export async function apiRequest(path, options = {}) {
  const response = await fetch(`${apiBase}/api${path}`, {
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...options.headers,
    },
  })
  const payload = response.status === 204 ? null : await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(errorMessage(payload, `API エラー (${response.status})`))
  }
  return payload
}

export function apiResourceUrl(path) {
  return `${apiBase}/api${path}`
}

export function uploadFile(path, file, onProgress = () => {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open('POST', `${apiBase}/api${path}`)
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress(Math.round((event.loaded / event.total) * 100))
      }
    }
    request.onerror = () => reject(new Error('アップロード中に通信エラーが発生しました'))
    request.onload = () => {
      let payload
      try {
        payload = JSON.parse(request.responseText)
      } catch {
        payload = null
      }
      if (request.status >= 200 && request.status < 300) {
        resolve(payload)
      } else {
        reject(new Error(errorMessage(payload, `アップロードに失敗しました (${request.status})`)))
      }
    }
    const formData = new FormData()
    formData.append('file', file)
    request.send(formData)
  })
}

export function liveSocketUrl() {
  const url = apiBase ? new URL(apiBase, window.location.href) : new URL(window.location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  url.pathname = '/ws/live'
  url.search = ''
  return url.toString()
}
