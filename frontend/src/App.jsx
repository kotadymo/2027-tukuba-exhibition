import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiRequest, apiResourceUrl, liveSocketUrl, uploadFile } from './services/api.js'
import './App.css'

const navigation = [
  { id: 'dashboard', label: 'ダッシュボード', icon: '◉' },
  { id: 'models', label: 'モデル管理', icon: '▣' },
  { id: 'settings', label: '設定', icon: '⚙' },
  { id: 'alerts', label: 'アラート履歴', icon: '◷' },
]

const statusNames = {
  connecting: '接続中',
  connected: '接続済み',
  disconnected: '未接続',
  error: 'エラー',
  ready: '待機中',
  playing: '再生中',
  paused: '一時停止中',
  ended: '再生終了',
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '不明'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function formatTime(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(milliseconds)) {
    return '--:--'
  }
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  const clock = `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
  return hours > 0 ? `${hours}:${clock}` : clock
}

function formatDate(value) {
  if (!value) return '不明'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '不明' : date.toLocaleString('ja-JP')
}

function statusClass(status) {
  if (status === 'connected' || status === 'playing') return 'is-good'
  if (status === 'connecting' || status === 'paused' || status === 'ready') return 'is-waiting'
  if (status === 'error') return 'is-error'
  return 'is-muted'
}

function CanvasView({ frame, settings, alerts, message }) {
  const containerRef = useRef(null)
  const canvasRef = useRef(null)
  const drawRef = useRef(() => {})

  useEffect(() => {
    const container = containerRef.current
    if (!container) return undefined
    const observer = new ResizeObserver(() => drawRef.current())
    observer.observe(container)
    window.addEventListener('resize', drawRef.current)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', drawRef.current)
    }
  }, [])

  useEffect(() => {
    drawRef.current = () => {
      const canvas = canvasRef.current
      const container = containerRef.current
      if (!canvas || !container) return
      const bounds = container.getBoundingClientRect()
      const ratio = window.devicePixelRatio || 1
      canvas.width = Math.max(1, Math.round(bounds.width * ratio))
      canvas.height = Math.max(1, Math.round(bounds.height * ratio))
      const context = canvas.getContext('2d')
      if (!context) return
      context.setTransform(ratio, 0, 0, ratio, 0, 0)
      context.fillStyle = '#080d12'
      context.fillRect(0, 0, bounds.width, bounds.height)
      if (!frame) return

      const image = new Image()
      image.onload = () => {
        const scale = Math.min(bounds.width / frame.meta.width, bounds.height / frame.meta.height)
        const drawWidth = frame.meta.width * scale
        const drawHeight = frame.meta.height * scale
        const offsetX = (bounds.width - drawWidth) / 2
        const offsetY = (bounds.height - drawHeight) / 2
        context.drawImage(image, offsetX, offsetY, drawWidth, drawHeight)

        const highlightedClasses = new Set(
          alerts.flatMap((alert) => alert.test ? [] : alert.detected_classes || []),
        )
        for (const detection of frame.meta.detections || []) {
          const [x1, y1, x2, y2] = detection.bbox
          const left = offsetX + x1 * scale
          const top = offsetY + y1 * scale
          const boxWidth = Math.max(0, (x2 - x1) * scale)
          const boxHeight = Math.max(0, (y2 - y1) * scale)
          const highlighted = highlightedClasses.has(detection.class_name)
          const color = highlighted
            ? '#ff4d67'
            : `hsl(${(detection.class_id * 71 + 152) % 360} 88% 64%)`
          if (settings?.show_boxes !== false) {
            context.strokeStyle = color
            context.lineWidth = highlighted ? 3 : 2
            context.strokeRect(left, top, boxWidth, boxHeight)
          }
          if (settings?.show_labels !== false) {
            const confidence = settings?.show_confidence === false
              ? ''
              : ` ${(detection.confidence * 100).toFixed(0)}%`
            const label = `${detection.class_name}${confidence}`
            context.font = '600 13px "Segoe UI", sans-serif'
            const labelWidth = context.measureText(label).width + 12
            const labelHeight = 24
            const labelTop = Math.max(0, top - labelHeight)
            context.fillStyle = color
            context.fillRect(left, labelTop, labelWidth, labelHeight)
            context.fillStyle = '#071014'
            context.fillText(label, left + 6, labelTop + 16)
          }
        }
      }
      image.src = frame.url
    }
    drawRef.current()
  }, [alerts, frame, settings])

  return (
    <div className="video-stage" ref={containerRef}>
      <canvas ref={canvasRef} aria-label="推論フレームと物体検出結果" />
      {!frame && (
        <div className="video-empty">
          <span className="empty-glyph">◉</span>
          <strong>{message.title}</strong>
          <span>{message.description}</span>
        </div>
      )}
      {frame && (
        <div className="frame-badge">
          <span className="live-dot" />
          {frame.meta.source_type === 'video' ? '動画推論' : 'ライブ推論'}
          <span>#{frame.meta.frame_id}</span>
        </div>
      )}
    </div>
  )
}

function RuleEditor({ rule, classes, onSave, onCancel, saving }) {
  const [name, setName] = useState(rule?.name || '')
  const [selectedClasses, setSelectedClasses] = useState(rule?.class_names || [])
  const [detectionDuration, setDetectionDuration] = useState(rule?.detection_duration_ms ?? 500)
  const [releaseDuration, setReleaseDuration] = useState(rule?.release_duration_ms ?? 2000)
  const [cooldown, setCooldown] = useState(rule?.cooldown_ms ?? 10000)
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)

  const toggleClass = (className) => {
    setSelectedClasses((current) => current.includes(className)
      ? current.filter((value) => value !== className)
      : [...current, className])
  }

  const submit = (event) => {
    event.preventDefault()
    onSave({
      name,
      class_names: selectedClasses,
      enabled,
      detection_duration_ms: Number(detectionDuration),
      release_duration_ms: Number(releaseDuration),
      cooldown_ms: Number(cooldown),
    })
  }

  return (
    <form className="rule-editor" onSubmit={submit}>
      <div className="form-heading">
        <div>
          <span className="eyebrow">{rule ? 'RULE EDITOR' : 'NEW RULE'}</span>
          <h3>{rule ? 'ルールを編集' : 'アラートルールを追加'}</h3>
        </div>
        <button className="icon-button" type="button" onClick={onCancel} aria-label="閉じる">×</button>
      </div>
      <label className="field">
        <span>ルール名</span>
        <input
          maxLength="120"
          onChange={(event) => setName(event.target.value)}
          placeholder="例：手とドリル"
          required
          value={name}
        />
      </label>
      <fieldset className="class-picker">
        <legend>必要なクラス <small>2種類以上</small></legend>
        {classes.length ? (
          <div className="class-options">
            {classes.map((item) => (
              <label className="check-option" key={item.class_id}>
                <input
                  checked={selectedClasses.includes(item.class_name)}
                  onChange={() => toggleClass(item.class_name)}
                  type="checkbox"
                />
                <span>{item.class_name}</span>
              </label>
            ))}
          </div>
        ) : (
          <p className="subtle-text">モデルを有効化するとクラスを選択できます。</p>
        )}
      </fieldset>
      <div className="duration-grid">
        <label className="field">
          <span>成立まで (ms)</span>
          <input
            max="600000"
            min="0"
            onChange={(event) => setDetectionDuration(event.target.value)}
            type="number"
            value={detectionDuration}
          />
        </label>
        <label className="field">
          <span>解除まで (ms)</span>
          <input
            max="600000"
            min="0"
            onChange={(event) => setReleaseDuration(event.target.value)}
            type="number"
            value={releaseDuration}
          />
        </label>
        <label className="field">
          <span>クールダウン (ms)</span>
          <input
            max="3600000"
            min="0"
            onChange={(event) => setCooldown(event.target.value)}
            type="number"
            value={cooldown}
          />
        </label>
      </div>
      <label className="toggle-row compact-toggle">
        <span>このルールを有効にする</span>
        <input checked={enabled} onChange={(event) => setEnabled(event.target.checked)} type="checkbox" />
        <i />
      </label>
      <div className="form-actions">
        <button className="button button-quiet" onClick={onCancel} type="button">キャンセル</button>
        <button
          className="button button-primary"
          disabled={saving || selectedClasses.length < 2}
          type="submit"
        >
          {saving ? '保存中…' : 'ルールを保存'}
        </button>
      </div>
    </form>
  )
}

function App() {
  const [page, setPage] = useState('dashboard')
  const [loading, setLoading] = useState(true)
  const [apiError, setApiError] = useState('')
  const [alertSaveError, setAlertSaveError] = useState('')
  const [health, setHealth] = useState(null)
  const [camera, setCamera] = useState(null)
  const [insta360, setInsta360] = useState(null)
  const [settings, setSettings] = useState(null)
  const [models, setModels] = useState([])
  const [activeModel, setActiveModel] = useState(null)
  const [videos, setVideos] = useState([])
  const [playback, setPlayback] = useState(null)
  const [rules, setRules] = useState([])
  const [history, setHistory] = useState([])
  const [alertDetail, setAlertDetail] = useState(null)
  const [alertDetailLoading, setAlertDetailLoading] = useState(false)
  const [activeAlerts, setActiveAlerts] = useState([])
  const [socketStatus, setSocketStatus] = useState('connecting')
  const [frame, setFrame] = useState(null)
  const [streamError, setStreamError] = useState(null)
  const [clock, setClock] = useState(0)
  const [audioUnlocked, setAudioUnlocked] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(null)
  const [uploadMessage, setUploadMessage] = useState('')
  const [editingRule, setEditingRule] = useState(undefined)
  const [savingRule, setSavingRule] = useState(false)
  const [insta360Draft, setInsta360Draft] = useState(null)
  const [settingsDraft, setSettingsDraft] = useState(null)
  const [savingSettings, setSavingSettings] = useState(false)
  const frameRef = useRef(null)
  const cameraSourceRef = useRef(null)
  const audioContextRef = useRef(null)
  const audioUnlockedRef = useRef(false)
  const settingsRef = useRef(null)
  const playSoundRef = useRef(() => {})

  const refreshData = useCallback(async () => {
    try {
      const [healthResult, sourceResult, insta360Result, settingsResult, modelsResult, activeModelResult,
        videosResult, playbackResult, rulesResult, historyResult] = await Promise.all([
        apiRequest('/health'),
        apiRequest('/source'),
        apiRequest('/insta360'),
        apiRequest('/settings'),
        apiRequest('/models'),
        apiRequest('/models/active'),
        apiRequest('/videos'),
        apiRequest('/videos/playback'),
        apiRequest('/rules'),
        apiRequest('/alerts?limit=50&offset=0'),
      ])
      setHealth(healthResult)
      cameraSourceRef.current = sourceResult.source_type
      setCamera(sourceResult)
      setInsta360(insta360Result)
      setSettings(settingsResult)
      setModels(modelsResult)
      setActiveModel(activeModelResult)
      setVideos(videosResult)
      setPlayback(playbackResult)
      setRules(rulesResult)
      setHistory(historyResult.items || [])
      setSettingsDraft((current) => current || settingsResult)
      setInsta360Draft((current) => current || {
        camera_ip: insta360Result.camera_ip,
        tcp_port: insta360Result.tcp_port,
        reconnect_interval_ms: insta360Result.reconnect_interval_ms,
      })
      setApiError('')
      setLoading(false)
    } catch (error) {
      setApiError(error.message)
      setLoading(false)
      throw error
    }
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => refreshData().catch(() => {}), 0)
    return () => window.clearTimeout(timer)
  }, [refreshData])

  useEffect(() => {
    settingsRef.current = settings
  }, [settings])

  useEffect(() => {
    playSoundRef.current = () => {
      if (!audioUnlockedRef.current || !settingsRef.current?.audio_enabled) return
      const context = audioContextRef.current
      if (!context || context.state !== 'running') return
      const oscillator = context.createOscillator()
      const gain = context.createGain()
      oscillator.type = 'triangle'
      oscillator.frequency.setValueAtTime(880, context.currentTime)
      oscillator.frequency.setValueAtTime(660, context.currentTime + 0.13)
      gain.gain.setValueAtTime(0.0001, context.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.16, context.currentTime + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.38)
      oscillator.connect(gain)
      gain.connect(context.destination)
      oscillator.start()
      oscillator.stop(context.currentTime + 0.4)
    }
  }, [])

  useEffect(() => {
    let disposed = false
    let socket
    let retryTimer
    let retryDelay = 1000

    const clearCurrentFrame = () => {
      if (frameRef.current?.url) URL.revokeObjectURL(frameRef.current.url)
      frameRef.current = null
      setFrame(null)
    }

    const handleTextMessage = (message) => {
      let event
      try {
        event = JSON.parse(message)
      } catch (error) {
        console.error('Invalid WebSocket event', error)
        setStreamError({ message: 'Backend から不正なイベントを受信しました' })
        return
      }
      if (event.type === 'insta360_status') {
        const isInsta360Source = cameraSourceRef.current === 'insta360'
        setInsta360((current) => current ? {
          ...current,
          status: event.status,
          error: event.error,
          last_frame_received_at: event.last_frame_received_at,
        } : current)
        setCamera((current) => {
          return current ? {
            ...current,
            insta360_status: event.status,
            insta360_error: event.error,
            status: isInsta360Source ? event.status : current.status,
          } : current
        })
        if (isInsta360Source && event.status !== 'connected') {
          clearCurrentFrame()
          setActiveAlerts([])
        }
      } else if (event.type === 'video_status') {
        setPlayback((current) => current ? {
          ...current,
          status: event.status,
          position_ms: event.position_ms,
          duration_ms: event.duration_ms,
          loop: event.loop,
          error: event.error,
        } : current)
        setCamera((current) => current ? {
          ...current,
          video_status: event.status,
          status: cameraSourceRef.current === 'video' ? event.status : current.status,
        } : current)
        if (event.reset_frame) {
          clearCurrentFrame()
          setActiveAlerts([])
          refreshData().catch(() => {})
        }
        if (event.status === 'error') {
          clearCurrentFrame()
          setStreamError({ code: 'video_error', message: event.error || '動画を再生できません' })
        }
      } else if (event.type === 'source_changed') {
        cameraSourceRef.current = event.source_type
        setCamera((current) => current ? {
          ...current,
          source_type: event.source_type,
        } : current)
        clearCurrentFrame()
        setStreamError(null)
        setActiveAlerts([])
        refreshData().catch(() => {})
      } else if (event.type === 'alert_started') {
        const alert = {
          ...event,
          id: event.alert_id || (event.test ? 'test-alert' : `${event.rule_id}-${event.timestamp}`),
        }
        setActiveAlerts((current) => [
          ...current.filter((item) => item.id !== alert.id),
          alert,
        ])
        playSoundRef.current()
        if (!event.test) refreshData().catch(() => {})
      } else if (event.type === 'alert_cleared') {
        setActiveAlerts((current) => current.filter((alert) => alert.rule_id !== event.rule_id))
        refreshData().catch(() => {})
      } else if (event.type === 'model_changed') {
        clearCurrentFrame()
        setActiveAlerts([])
        refreshData().catch(() => {})
      } else if (event.type === 'rules_changed') {
        refreshData().catch(() => {})
      } else if (event.type === 'alert_save_error') {
        setAlertSaveError(event.message || 'アラート履歴を保存できませんでした')
      } else if (event.type === 'error') {
        if (event.cleared) {
          setStreamError(null)
        } else {
          if (['stream_stale', 'inference_failed', 'active_model_load_failed'].includes(event.code)) {
            clearCurrentFrame()
          }
          setStreamError({ code: event.code, message: event.message || '映像処理でエラーが発生しました' })
        }
      }
    }

    const handleBinaryMessage = (data) => {
      try {
        if (!(data instanceof ArrayBuffer) || data.byteLength < 4) {
          throw new Error('フレームデータの形式が不正です')
        }
        const headerLength = new DataView(data).getUint32(0, true)
        if (headerLength > data.byteLength - 4) {
          throw new Error('フレームヘッダーが破損しています')
        }
        const headerBytes = new Uint8Array(data, 4, headerLength)
        const metadata = JSON.parse(new TextDecoder().decode(headerBytes))
        const jpeg = data.slice(4 + headerLength)
        const nextFrame = {
          url: URL.createObjectURL(new Blob([jpeg], { type: 'image/jpeg' })),
          meta: metadata,
          receivedAt: Date.now(),
        }
        if (frameRef.current?.url) URL.revokeObjectURL(frameRef.current.url)
        frameRef.current = nextFrame
        setFrame(nextFrame)
        setStreamError((current) => current?.code === 'stream_stale' ? null : current)
        if (metadata.source_type === 'video') {
          setPlayback((current) => current ? {
            ...current,
            position_ms: metadata.media_time_ms,
          } : current)
        }
      } catch (error) {
        console.error('Could not decode WebSocket frame', error)
        setStreamError({ message: `映像フレームを表示できません: ${error.message}` })
      }
    }

    const connect = () => {
      if (disposed) return
      setSocketStatus('connecting')
      try {
        socket = new WebSocket(liveSocketUrl())
        socket.binaryType = 'arraybuffer'
      } catch (error) {
        setSocketStatus('disconnected')
        setApiError(`WebSocket に接続できません: ${error.message}`)
        retryTimer = window.setTimeout(connect, retryDelay)
        retryDelay = Math.min(retryDelay * 1.6, 8000)
        return
      }
      socket.onopen = () => {
        retryDelay = 1000
        setSocketStatus('connected')
        refreshData().catch(() => {})
      }
      socket.onmessage = (event) => {
        if (typeof event.data === 'string') handleTextMessage(event.data)
        else handleBinaryMessage(event.data)
      }
      socket.onerror = () => setSocketStatus('disconnected')
      socket.onclose = () => {
        if (disposed) return
        setSocketStatus('disconnected')
        clearCurrentFrame()
        retryTimer = window.setTimeout(connect, retryDelay)
        retryDelay = Math.min(retryDelay * 1.6, 8000)
      }
    }

    connect()
    return () => {
      disposed = true
      window.clearTimeout(retryTimer)
      if (socket) {
        socket.onclose = null
        socket.close()
      }
      if (frameRef.current?.url) URL.revokeObjectURL(frameRef.current.url)
      frameRef.current = null
    }
  }, [refreshData])

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 500)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (camera?.source_type !== 'video') return undefined
    const timer = window.setInterval(() => {
      apiRequest('/videos/playback')
        .then(setPlayback)
        .catch((error) => setApiError(error.message))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [camera?.source_type])

  const videoPaused = camera?.source_type === 'video'
    && ['paused', 'ended', 'ready'].includes(playback?.status)
  const staleFrame = Boolean(frame && !videoPaused && clock - frame.receivedAt > 2500)
  const shownFrame = staleFrame ? null : frame
  const detectionFps = frame?.meta.inference_fps || 0
  const processingMs = frame?.meta.processing_ms
  const currentVideo = videos.find((video) => video.id === camera?.active_video_id)
  const latestAlert = activeAlerts.at(-1)
  const alertCount = history.length

  const handleAction = async (action, successMessage = '') => {
    try {
      const result = await action()
      if (successMessage) setApiError('')
      return result
    } catch (error) {
      setApiError(error.message)
      return null
    }
  }

  const changeSource = (sourceType) => handleAction(async () => {
    const nextCamera = await apiRequest('/source', {
      method: 'PATCH',
      body: JSON.stringify({ source_type: sourceType }),
    })
    cameraSourceRef.current = nextCamera.source_type
    setCamera(nextCamera)
    return refreshData()
  })

  const connectInsta360 = () => handleAction(async () => {
    const result = await apiRequest('/insta360/connect', { method: 'POST' })
    setInsta360(result)
    await refreshData()
    return result
  })

  const disconnectInsta360 = () => handleAction(async () => {
    const result = await apiRequest('/insta360/disconnect', { method: 'POST' })
    setInsta360(result)
    await refreshData()
    return result
  })

  const saveInsta360 = (event) => {
    event.preventDefault()
    handleAction(async () => {
      const result = await apiRequest('/insta360', {
        method: 'PATCH',
        body: JSON.stringify(insta360Draft),
      })
      setInsta360(result)
      setInsta360Draft({
        camera_ip: result.camera_ip,
        tcp_port: result.tcp_port,
        reconnect_interval_ms: result.reconnect_interval_ms,
      })
    })
  }

  const activateVideo = (videoId) => handleAction(async () => {
    if (!videoId) return
    const result = await apiRequest(`/videos/${videoId}/activate`, { method: 'POST' })
    setPlayback(result)
    await refreshData()
  })

  const playbackAction = (action) => handleAction(async () => {
    const result = await apiRequest('/videos/playback', {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    setPlayback(result)
  })

  const setLoop = (enabled) => handleAction(async () => {
    const result = await apiRequest('/videos/playback', {
      method: 'PATCH',
      body: JSON.stringify({ loop: enabled }),
    })
    setPlayback(result)
    await refreshData()
  })

  const handleVideoUpload = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploadMessage('')
    setUploadProgress(0)
    try {
      const video = await uploadFile('/videos', file, setUploadProgress)
      setUploadMessage(`${video.name} を登録し、再生待機状態にしました`)
      await refreshData()
    } catch (error) {
      setUploadMessage(error.message)
    } finally {
      setUploadProgress(null)
    }
  }

  const selectVideo = (event) => {
    const videoId = Number(event.target.value)
    if (videoId) activateVideo(videoId)
  }

  const removeVideo = (videoId) => handleAction(async () => {
    await apiRequest(`/videos/${videoId}`, { method: 'DELETE' })
    await refreshData()
  })

  const saveRule = async (payload) => {
    setSavingRule(true)
    try {
      const ruleId = editingRule?.id
      await apiRequest(ruleId ? `/rules/${ruleId}` : '/rules', {
        method: ruleId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      })
      setEditingRule(undefined)
      await refreshData()
    } catch (error) {
      setApiError(error.message)
    } finally {
      setSavingRule(false)
    }
  }

  const toggleRule = (rule) => handleAction(async () => {
    await apiRequest(`/rules/${rule.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: rule.name,
        class_names: rule.class_names,
        enabled: !rule.enabled,
        detection_duration_ms: rule.detection_duration_ms,
        release_duration_ms: rule.release_duration_ms,
        cooldown_ms: rule.cooldown_ms,
      }),
    })
    await refreshData()
  })

  const deleteRule = (rule) => {
    if (!window.confirm(`「${rule.name}」を削除しますか？`)) return
    handleAction(async () => {
      await apiRequest(`/rules/${rule.id}`, { method: 'DELETE' })
      await refreshData()
    })
  }

  const removeModel = (model) => {
    if (!window.confirm(`「${model.name}」を削除しますか？`)) return
    handleAction(async () => {
      await apiRequest(`/models/${model.id}`, { method: 'DELETE' })
      await refreshData()
    })
  }

  const uploadModel = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploadMessage('')
    setUploadProgress(0)
    try {
      const model = await uploadFile('/models', file, setUploadProgress)
      setUploadMessage(`${model.name} を検証・登録しました。有効化はモデル一覧から行ってください。`)
      await refreshData()
    } catch (error) {
      setUploadMessage(error.message)
    } finally {
      setUploadProgress(null)
    }
  }

  const activateModel = (model) => handleAction(async () => {
    await apiRequest(`/models/${model.id}/activate`, { method: 'POST' })
    setEditingRule(undefined)
    await refreshData()
  })

  const unlockAudio = async () => {
    try {
      if (!audioContextRef.current) {
        audioContextRef.current = new window.AudioContext()
      }
      await audioContextRef.current.resume()
      audioUnlockedRef.current = true
      setAudioUnlocked(true)
      setApiError('')
    } catch (error) {
      setApiError(`音声を有効化できません: ${error.message}`)
    }
  }

  const toggleAudio = () => handleAction(async () => {
    const result = await apiRequest('/settings', {
      method: 'PATCH',
      body: JSON.stringify({ audio_enabled: !settings?.audio_enabled }),
    })
    setSettings(result)
    setSettingsDraft(result)
  })

  const testAlert = () => handleAction(async () => {
    await apiRequest('/alerts/test', { method: 'POST' })
  })

  const saveSettings = (event) => {
    event.preventDefault()
    handleAction(async () => {
      setSavingSettings(true)
      try {
        const { confidence, iou_threshold, imgsz, target_fps, jpeg_quality, show_boxes, show_labels,
          show_confidence, audio_enabled } = settingsDraft
        const result = await apiRequest('/settings', {
          method: 'PATCH',
          body: JSON.stringify({
            confidence,
            iou_threshold,
            imgsz,
            target_fps,
            jpeg_quality,
            show_boxes,
            show_labels,
            show_confidence,
            audio_enabled,
          }),
        })
        setSettings(result)
        setSettingsDraft(result)
      } finally {
        setSavingSettings(false)
      }
    })
  }

  const changeSetting = (key, value) => {
    setSettingsDraft((current) => ({ ...current, [key]: value }))
  }

  const changeInferenceNumber = (key, value) => {
    const number = value === '' ? '' : Number(value)
    changeSetting(key, number)
  }

  const loadAlertDetail = async (alertId) => {
    setAlertDetail(null)
    setAlertDetailLoading(true)
    try {
      const result = await apiRequest(`/alerts/${alertId}`)
      setAlertDetail(result)
    } catch (error) {
      setApiError(error.message)
    } finally {
      setAlertDetailLoading(false)
    }
  }

  const deleteAlertHistory = () => {
    if (!window.confirm('すべてのアラート履歴と保存画像を削除しますか？この操作は取り消せません。')) return
    handleAction(async () => {
      await apiRequest('/alerts', { method: 'DELETE' })
      setAlertDetail(null)
      await refreshData()
    })
  }

  const streamMessage = useMemo(() => {
    if (streamError?.message) {
      return { title: '映像ストリームに問題があります', description: streamError.message }
    }
    if (loading) return { title: 'Backend に接続しています', description: '状態を確認しています…' }
    if (socketStatus !== 'connected') {
      return { title: 'Backend に再接続しています', description: 'WebSocket 接続を自動で再試行しています' }
    }
    if (!activeModel) return { title: '推論モデルが選択されていません', description: 'モデル管理から .pt モデルを有効化してください' }
    if (activeModel.load_error) return { title: 'モデルを読み込めません', description: activeModel.load_error }
    if (camera?.source_type === 'video') {
      if (!currentVideo) return { title: '動画を選択してください', description: 'MP4 をアップロードするか登録済み動画を選択してください' }
      if (playback?.status === 'paused') return { title: '一時停止中', description: '最後に推論したフレームを表示しています' }
      if (playback?.status === 'ended') return { title: '再生終了', description: 'ループを有効にするか、先頭から再生してください' }
      if (playback?.status === 'error') return { title: '動画を再生できません', description: playback.error || '動画デコードに失敗しました' }
      if (playback?.status === 'ready') return { title: '再生待機中', description: '再生ボタンを押すと CPU 推論を開始します' }
    } else {
      if (camera?.status === 'error') return { title: 'カメラに接続できません', description: camera.insta360_error || 'GO Ultra の Wi-Fi 接続と設定を確認してください' }
      if (camera?.status !== 'connected') return { title: 'カメラ未接続', description: '展示 PC を GO Ultra の Wi-Fi に接続し、接続を開始してください' }
    }
    if (staleFrame) return { title: '推論フレームが届いていません', description: '入力ソースまたは CPU 推論の状態を確認してください' }
    return { title: '映像フレームを待っています', description: '最初の推論結果を受信するとここに表示します' }
  }, [
    activeModel,
    camera,
    currentVideo,
    loading,
    playback,
    socketStatus,
    staleFrame,
    streamError,
  ])

  const alertPreview = useMemo(() => {
    if (!alertDetail?.frame) return null
    return {
      url: apiResourceUrl(`/alerts/${alertDetail.id}/frame`),
      meta: {
        type: 'frame',
        frame_id: alertDetail.frame.frame_id,
        source_session_id: alertDetail.frame.source_session_id,
        timestamp: alertDetail.frame.captured_at,
        source_type: alertDetail.frame.source_type,
        video_id: alertDetail.frame.video_id,
        media_time_ms: alertDetail.frame.media_time_ms,
        width: alertDetail.frame.width,
        height: alertDetail.frame.height,
        detections: alertDetail.frame.detections,
      },
    }
  }, [alertDetail])

  if (loading && !health) {
    return (
      <main className="boot-screen">
        <div className="boot-mark">T</div>
        <span className="eyebrow">TSUKUBA EXHIBITION · AI VISION</span>
        <h1>展示システムを起動しています</h1>
        <p>{apiError || 'Backend と SQLite の状態を確認しています。'}</p>
        <div className="loading-line"><span /></div>
      </main>
    )
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={(event) => { event.preventDefault(); setPage('dashboard') }}>
          <span className="brand-mark">T</span>
          <span>
            <strong>TSUKUBA</strong>
            <small>EXHIBITION · AI VISION</small>
          </span>
        </a>
        <div className="side-section-label">WORKSPACE</div>
        <nav className="side-nav" aria-label="メインナビゲーション">
          {navigation.map((item) => (
            <button
              className={`nav-item ${page === item.id ? 'is-active' : ''}`}
              key={item.id}
              onClick={() => setPage(item.id)}
              type="button"
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
              {item.id === 'alerts' && alertCount > 0 && <span className="nav-count">{alertCount}</span>}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="connection-card">
            <span className={`status-dot ${socketStatus === 'connected' ? 'is-online' : ''}`} />
            <div>
              <strong>Backend</strong>
              <small>{socketStatus === 'connected' ? 'リアルタイム接続中' : '再接続を試行中'}</small>
            </div>
          </div>
          <div className="sidebar-version">展示用システム <span>v0.5.1</span></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div>
            <span className="eyebrow">2027 TSUKUBA EXHIBITION</span>
            <h1>{navigation.find((item) => item.id === page)?.label}</h1>
          </div>
          <div className="topbar-actions">
            <div className={`backend-pill ${socketStatus === 'connected' ? 'is-online' : ''}`}>
              <span className="status-dot" />
              {socketStatus === 'connected' ? 'システム稼働中' : '接続待ち'}
            </div>
            <button className="icon-button refresh-button" onClick={() => refreshData().catch(() => {})} type="button" title="再読み込み">
              ↻
            </button>
          </div>
        </header>

        {apiError && (
          <div className="notice notice-error" role="alert">
            <span>!</span>
            <p>{apiError}</p>
            <button className="icon-button" onClick={() => setApiError('')} type="button" aria-label="閉じる">×</button>
          </div>
        )}
        {alertSaveError && (
          <div className="notice notice-error" role="alert">
            <span>!</span>
            <p>{alertSaveError}</p>
            <button className="icon-button" onClick={() => setAlertSaveError('')} type="button" aria-label="閉じる">×</button>
          </div>
        )}

        {activeAlerts.length > 0 && (
          <section className="alert-stack" aria-live="assertive">
            {activeAlerts.map((alert) => (
              <div className="alert-banner" key={alert.id}>
                <span className="alert-pulse">!</span>
                <div>
                  <strong>{alert.rule_name || 'アラート'}</strong>
                  <span>
                    {alert.test ? '音声・画面のテスト通知' : (alert.detected_classes || []).join(' ＋ ')}
                    {alert.timestamp && ` · ${formatDate(alert.timestamp)}`}
                  </span>
                </div>
                <button
                  className="icon-button"
                  onClick={() => setActiveAlerts((current) => current.filter((item) => item.id !== alert.id))}
                  type="button"
                  aria-label="警告を閉じる"
                >×</button>
              </div>
            ))}
          </section>
        )}

        {page === 'dashboard' && (
          <div className="page-content dashboard-page">
            <section className="metrics-grid">
              <div className="metric-card">
                <span className="metric-label">入力ソース</span>
                <strong>{camera?.source_type === 'video' ? 'アップロード動画' : 'Insta360 GO Ultra'}</strong>
                <span className={`metric-status ${statusClass(camera?.status)}`}>
                  <i />{statusNames[camera?.status] || '状態不明'}
                </span>
              </div>
              <div className="metric-card">
                <span className="metric-label">有効モデル</span>
                <strong className="metric-truncate">{activeModel?.name || '未選択'}</strong>
                <span className={`metric-status ${activeModel?.loaded ? 'is-good' : 'is-muted'}`}>
                  <i />{activeModel?.loaded ? 'CPU 推論準備完了' : 'モデル待機中'}
                </span>
              </div>
              <div className="metric-card">
                <span className="metric-label">推論 FPS</span>
                <strong>{detectionFps.toFixed(1)}<small> FPS</small></strong>
                <span className="metric-caption">目標 {settings?.target_fps ?? 5} FPS</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">処理時間</span>
                <strong>{processingMs === undefined ? '--' : processingMs.toFixed(1)}<small> ms</small></strong>
                <span className="metric-caption">{frame ? `${frame.meta.width} × ${frame.meta.height}` : '推論フレーム待機中'}</span>
              </div>
            </section>

            <div className="dashboard-grid">
              <section className="panel video-panel">
                <div className="panel-heading">
                  <div>
                    <span className="eyebrow">LIVE MONITOR</span>
                    <h2>{camera?.source_type === 'video' ? currentVideo?.name || '動画入力' : 'カメラ映像'}</h2>
                  </div>
                  <div className="panel-heading-actions">
                    <span className={`state-chip ${statusClass(camera?.status)}`}>
                      <i />{statusNames[camera?.status] || '未接続'}
                    </span>
                    {frame && <span className="frame-time">{formatDate(frame.meta.timestamp)}</span>}
                  </div>
                </div>
                <CanvasView frame={shownFrame} settings={settings} alerts={activeAlerts} message={streamMessage} />
                <div className="frame-footer">
                  <span>
                    {frame?.meta.source_type === 'video'
                      ? `${formatTime(frame.meta.media_time_ms)} / ${formatTime(playback?.duration_ms)}`
                      : frame ? `FRAME ${frame.meta.frame_id}` : '入力待機中'}
                  </span>
                  <span>{frame?.meta.detections?.length || 0} 件の検出</span>
                </div>

                <div className="source-controls">
                  <div className="source-switch">
                    <span className="control-label">映像入力</span>
                    <div className="segmented-control">
                      <button
                        className={camera?.source_type === 'insta360' ? 'is-selected' : ''}
                        onClick={() => changeSource('insta360')}
                        type="button"
                      >Insta360 GO Ultra（ライブ）</button>
                      <button
                        className={camera?.source_type === 'video' ? 'is-selected' : ''}
                        onClick={() => changeSource('video')}
                        type="button"
                      >アップロード動画</button>
                    </div>
                  </div>
                  {camera?.source_type === 'video' ? (
                    <div className="media-controls">
                      <div className="media-control-buttons">
                        <button className="button button-primary" disabled={!currentVideo} onClick={() => playbackAction('play')} type="button">
                          ▶ 再生
                        </button>
                        <button className="button button-quiet" disabled={!currentVideo} onClick={() => playbackAction('pause')} type="button">
                          Ⅱ 一時停止
                        </button>
                        <button className="button button-quiet" disabled={!currentVideo} onClick={() => playbackAction('restart')} type="button">
                          ↺ 先頭から
                        </button>
                        <button className="button button-quiet" disabled={!currentVideo} onClick={() => playbackAction('stop')} type="button">
                          ■ 停止
                        </button>
                      </div>
                      <label className="toggle-row loop-toggle">
                        <span>ループ再生</span>
                        <input checked={playback?.loop ?? true} onChange={(event) => setLoop(event.target.checked)} type="checkbox" />
                        <i />
                      </label>
                    </div>
                  ) : (
                    <div className="camera-controls">
                      <span className="source-url">
                        {insta360?.camera_ip}:{insta360?.tcp_port}
                        {insta360?.error ? ` · ${insta360.error}` : ''}
                      </span>
                      {camera?.status === 'connected' || camera?.status === 'connecting' ? (
                        <button className="button button-quiet" onClick={disconnectInsta360} type="button">切断</button>
                      ) : (
                        <button className="button button-primary" onClick={connectInsta360} type="button">接続開始</button>
                      )}
                    </div>
                  )}
                  {camera?.source_type === 'video' && (
                    <div className="video-library-controls">
                      <label className="field video-select">
                        <span>登録済み動画を選択</span>
                        <select onChange={selectVideo} value={camera?.active_video_id || ''}>
                          <option value="">動画を選択…</option>
                          {videos.map((video) => (
                            <option key={video.id} value={video.id}>
                              {video.name} · {formatTime(video.duration_ms)}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="button button-outline upload-button">
                        <input accept=".mp4,video/mp4" onChange={handleVideoUpload} type="file" />
                        ＋ MP4 を追加
                      </label>
                    </div>
                  )}
                  {camera?.source_type === 'video' && currentVideo && (
                    <div className="video-details">
                      <span>{currentVideo.width && currentVideo.height ? `${currentVideo.width} × ${currentVideo.height}` : '解像度不明'}</span>
                      <span>{currentVideo.fps ? `${currentVideo.fps.toFixed(2)} FPS` : 'FPS 不明'}</span>
                      <span>{formatBytes(currentVideo.file_size)}</span>
                      <button
                        className="text-button danger-text"
                        disabled={playback?.status === 'playing'}
                        onClick={() => removeVideo(currentVideo.id)}
                        type="button"
                      >動画を削除</button>
                    </div>
                  )}
                  {uploadProgress !== null && (
                    <div className="upload-progress">
                      <div><span>アップロード中</span><strong>{uploadProgress}%</strong></div>
                      <progress max="100" value={uploadProgress} />
                    </div>
                  )}
                  {uploadMessage && <p className="inline-message">{uploadMessage}</p>}
                </div>
              </section>

              <aside className="dashboard-side">
                <section className="panel alert-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">ALERT CENTER</span>
                      <h2>警告・音声</h2>
                    </div>
                    <span className="alert-count">{activeAlerts.filter((item) => !item.test).length}</span>
                  </div>
                  {latestAlert ? (
                    <div className="current-alert">
                      <span className="alert-emblem">!</span>
                      <div>
                        <strong>{latestAlert.rule_name}</strong>
                        <span>{(latestAlert.detected_classes || []).join(' ＋ ') || 'テスト通知'}</span>
                      </div>
                    </div>
                  ) : (
                    <div className="no-alert">
                      <span>✓</span>
                      <div><strong>アラートなし</strong><small>ルール成立時にここへ表示します</small></div>
                    </div>
                  )}
                  <div className="audio-actions">
                    <button className="button button-outline" onClick={audioUnlocked ? toggleAudio : unlockAudio} type="button">
                      {audioUnlocked ? `音声 ${settings?.audio_enabled ? 'ON' : 'OFF'}` : '♫ 音声を有効化'}
                    </button>
                    <button className="button button-quiet" onClick={testAlert} type="button">テスト発報</button>
                  </div>
                  <p className="helper-text">
                    {audioUnlocked
                      ? 'ブラウザ音声を有効化済みです。'
                      : '展示開始時に音声を有効化してください。'}
                  </p>
                </section>

                <section className="panel rules-panel">
                  <div className="panel-heading">
                    <div>
                      <span className="eyebrow">AND CONDITIONS</span>
                      <h2>アラートルール <span className="heading-count">{rules.length}</span></h2>
                    </div>
                    <button className="button button-small button-outline" onClick={() => setEditingRule(null)} type="button">
                      ＋ 追加
                    </button>
                  </div>
                  {editingRule !== undefined && (
                    <RuleEditor
                      classes={activeModel?.classes || []}
                      key={editingRule?.id || 'new-rule'}
                      onCancel={() => setEditingRule(undefined)}
                      onSave={saveRule}
                      rule={editingRule}
                      saving={savingRule}
                    />
                  )}
                  <div className="rule-list">
                    {rules.length === 0 && editingRule === undefined && (
                      <div className="empty-list">まだルールがありません。2種類以上のクラスを組み合わせて追加してください。</div>
                    )}
                    {rules.map((rule) => (
                      <article className={`rule-item ${rule.incompatible ? 'is-incompatible' : ''}`} key={rule.id}>
                        <div className="rule-main">
                          <label className="mini-switch" title={rule.enabled ? 'ルールを無効化' : 'ルールを有効化'}>
                            <input checked={rule.enabled} onChange={() => toggleRule(rule)} type="checkbox" />
                            <i />
                          </label>
                          <div className="rule-info">
                            <strong>{rule.name}</strong>
                            <div className="class-tags">
                              {rule.class_names.map((name) => <span key={name}>{name}</span>)}
                            </div>
                            {rule.incompatible && (
                              <small className="incompatible-note">
                                モデルにありません: {rule.missing_class_names.join(', ')}
                              </small>
                            )}
                          </div>
                          <span className={`rule-state state-${rule.state}`}>
                            {rule.incompatible ? '非互換' : rule.state === 'active' ? '発報中' : rule.state === 'pending' ? '判定中' : '待機'}
                          </span>
                        </div>
                        <div className="rule-foot">
                          <span>成立 {rule.detection_duration_ms} ms</span>
                          <span>解除 {rule.release_duration_ms} ms</span>
                          <div>
                            <button className="text-button" onClick={() => setEditingRule(rule)} type="button">編集</button>
                            <button className="text-button danger-text" onClick={() => deleteRule(rule)} type="button">削除</button>
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>

                <section className="panel recent-panel">
                  <div className="panel-heading">
                    <div><span className="eyebrow">RECENT EVENTS</span><h2>最近の発報</h2></div>
                    <button className="text-button" onClick={() => setPage('alerts')} type="button">すべて見る →</button>
                  </div>
                  {history.length ? (
                    <div className="recent-list">
                      {history.slice(0, 4).map((alert) => (
                        <div className="recent-row" key={alert.id}>
                          <span className="recent-marker" />
                          <div><strong>{alert.rule_name}</strong><small>{(alert.detected_classes || []).join(' ＋ ')}</small></div>
                          <time>{new Date(alert.created_at).toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' })}</time>
                        </div>
                      ))}
                    </div>
                  ) : <p className="empty-list">発報履歴はありません。</p>}
                </section>
              </aside>
            </div>
          </div>
        )}

        {page === 'models' && (
          <div className="page-content narrow-page">
            <section className="panel upload-panel">
              <div className="section-title">
                <div className="section-icon">↑</div>
                <div><span className="eyebrow">MODEL REGISTRY</span><h2>カスタム YOLO モデルを登録</h2></div>
              </div>
              <p className="section-description">
                Ultralytics で読み込める物体検出用の <code>.pt</code> ファイルをアップロードします。
                登録後に CPU で検証し、一覧から有効化してください。
              </p>
              <label className="dropzone model-dropzone">
                <input accept=".pt,application/octet-stream" onChange={uploadModel} type="file" />
                <span className="dropzone-icon">＋</span>
                <strong>.pt ファイルを選択</strong>
                <small>最大 {formatBytes(settings?.max_model_size_bytes || 512 * 1024 ** 2)}</small>
              </label>
              {uploadProgress !== null && (
                <div className="upload-progress">
                  <div><span>モデルをアップロードしています</span><strong>{uploadProgress}%</strong></div>
                  <progress max="100" value={uploadProgress} />
                </div>
              )}
              {uploadMessage && <p className="inline-message">{uploadMessage}</p>}
              <div className="trust-note">
                <strong>信頼できるモデルのみ登録してください</strong>
                <span>.pt の読み込みは任意コード実行の危険を伴う場合があります。提供元を確認したモデルを使用してください。</span>
              </div>
            </section>

            <section className="panel model-list-panel">
              <div className="panel-heading">
                <div><span className="eyebrow">REGISTERED MODELS</span><h2>登録済みモデル <span className="heading-count">{models.length}</span></h2></div>
                <span className="subtle-text">有効化できるモデルは1つです</span>
              </div>
              {activeModel?.load_error && <div className="notice notice-error compact-notice">{activeModel.load_error}</div>}
              {models.length === 0 ? (
                <div className="empty-state"><span>▣</span><strong>モデルがありません</strong><p>検証済みの .pt ファイルをアップロードしてください。</p></div>
              ) : (
                <div className="model-list">
                  {models.map((model) => (
                    <article className={`model-card ${model.active ? 'is-active' : ''}`} key={model.id}>
                      <div className="model-symbol">YOLO</div>
                      <div className="model-details">
                        <div className="model-title-row">
                          <h3>{model.name}</h3>
                          {model.active && <span className="active-badge">{model.loaded ? '推論中' : '有効・未ロード'}</span>}
                        </div>
                        <p>{model.original_filename} · {formatBytes(model.file_size)} · 登録 {formatDate(model.created_at)}</p>
                        <div className="class-tags model-tags">
                          {model.classes.map((item) => <span key={item.class_id}>{item.class_name}</span>)}
                        </div>
                      </div>
                      <div className="model-actions">
                        {!model.active && (
                          <button className="button button-primary" onClick={() => activateModel(model)} type="button">
                            このモデルを有効化
                          </button>
                        )}
                        <button
                          className="text-button danger-text"
                          disabled={model.active}
                          onClick={() => removeModel(model)}
                          type="button"
                        >削除</button>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {page === 'settings' && (
          <div className="page-content narrow-page settings-page">
            <section className="panel">
              <div className="section-title">
                <div className="section-icon">⌁</div>
                <div><span className="eyebrow">INSTA360 GO ULTRA</span><h2>ライブ入力設定</h2></div>
              </div>
              <form className="settings-form" onSubmit={saveInsta360}>
                <div className="settings-grid">
                  <label className="field">
                    <span>カメラ IPv4 アドレス</span>
                    <input
                      onChange={(event) => setInsta360Draft((current) => ({ ...current, camera_ip: event.target.value }))}
                      placeholder="192.168.42.1"
                      value={insta360Draft?.camera_ip ?? '192.168.42.1'}
                    />
                  </label>
                  <label className="field">
                    <span>TCP ポート</span>
                    <input
                      max="65535"
                      min="1"
                      onChange={(event) => setInsta360Draft((current) => ({ ...current, tcp_port: Number(event.target.value) }))}
                      type="number"
                      value={insta360Draft?.tcp_port ?? 6666}
                    />
                  </label>
                  <label className="field">
                    <span>再接続間隔 (ms)</span>
                    <input
                      max="60000"
                      min="500"
                      onChange={(event) => setInsta360Draft((current) => ({ ...current, reconnect_interval_ms: Number(event.target.value) }))}
                      type="number"
                      value={insta360Draft?.reconnect_interval_ms ?? 3000}
                    />
                  </label>
                </div>
                <p className="helper-text">GO Ultra の Wi-Fi には OS 側で接続してください。接続・切断はダッシュボードから操作できます。</p>
                <div className="form-actions">
                  <button className="button button-primary" type="submit">Insta360 設定を保存</button>
                  <span className={`state-chip ${statusClass(insta360?.status)}`}>
                    <i />{statusNames[insta360?.status] || '未接続'}
                  </span>
                </div>
              </form>
            </section>

            <section className="panel">
              <div className="section-title">
                <div className="section-icon">⌘</div>
                <div><span className="eyebrow">CPU INFERENCE</span><h2>推論設定</h2></div>
              </div>
              <form className="settings-form" onSubmit={saveSettings}>
                <div className="settings-grid">
                  <label className="field">
                    <span>Confidence</span>
                    <input max="1" min="0" onChange={(event) => changeInferenceNumber('confidence', event.target.value)} step="0.01" type="number" value={settingsDraft?.confidence ?? 0.4} />
                    <small>信頼度の下限 (0〜1)</small>
                  </label>
                  <label className="field">
                    <span>NMS IoU</span>
                    <input max="1" min="0" onChange={(event) => changeInferenceNumber('iou_threshold', event.target.value)} step="0.01" type="number" value={settingsDraft?.iou_threshold ?? 0.7} />
                    <small>重複ボックス抑制 (0〜1)</small>
                  </label>
                  <label className="field">
                    <span>画像サイズ (imgsz)</span>
                    <input max="2048" min="32" onChange={(event) => changeInferenceNumber('imgsz', event.target.value)} step="32" type="number" value={settingsDraft?.imgsz ?? 416} />
                    <small>モデル互換の整数値</small>
                  </label>
                  <label className="field">
                    <span>目標推論 FPS</span>
                    <input max="60" min="0.1" onChange={(event) => changeInferenceNumber('target_fps', event.target.value)} step="0.5" type="number" value={settingsDraft?.target_fps ?? 5} />
                    <small>上限目標。性能保証値ではありません。</small>
                  </label>
                  <label className="field">
                    <span>アラート画像 JPEG 品質</span>
                    <input max="100" min="1" onChange={(event) => changeInferenceNumber('jpeg_quality', event.target.value)} step="1" type="number" value={settingsDraft?.jpeg_quality ?? 80} />
                    <small>履歴に保存する画像の品質 (1〜100)</small>
                  </label>
                </div>
                <div className="display-options">
                  <span className="control-label">映像表示</span>
                  {[
                    ['show_boxes', 'Bounding Box'],
                    ['show_labels', 'クラス名'],
                    ['show_confidence', 'Confidence'],
                  ].map(([key, label]) => (
                    <label className="check-option" key={key}>
                      <input checked={settingsDraft?.[key] ?? true} onChange={(event) => changeSetting(key, event.target.checked)} type="checkbox" />
                      <span>{label}</span>
                    </label>
                  ))}
                </div>
                <div className="settings-footer">
                  <label className="toggle-row">
                    <span><strong>ブラウザ警告音</strong><small>音声の有効化はダッシュボードから操作します</small></span>
                    <input checked={settingsDraft?.audio_enabled ?? true} onChange={(event) => changeSetting('audio_enabled', event.target.checked)} type="checkbox" />
                    <i />
                  </label>
                  <button className="button button-primary" disabled={savingSettings} type="submit">
                    {savingSettings ? '保存中…' : '設定を保存'}
                  </button>
                </div>
              </form>
            </section>

            <section className="panel runtime-panel">
              <div><span className="eyebrow">RUNTIME</span><h2>実行環境</h2></div>
              <div className="runtime-grid">
                <span>推論デバイス</span><strong>CPU</strong>
                <span>Backend 稼働時間</span><strong>{health ? `${Math.floor(health.uptime_seconds / 60)} 分` : '不明'}</strong>
                <span>MP4 アップロード上限</span><strong>{formatBytes(settings?.max_video_size_bytes)}</strong>
                <span>.pt アップロード上限</span><strong>{formatBytes(settings?.max_model_size_bytes)}</strong>
              </div>
            </section>
          </div>
        )}

        {page === 'alerts' && (
          <div className="page-content narrow-page">
            <section className="panel history-panel">
              <div className="panel-heading">
                <div><span className="eyebrow">EVENT LOG</span><h2>アラート履歴</h2></div>
                <div className="panel-heading-actions">
                  <button className="button button-outline" onClick={() => refreshData().catch(() => {})} type="button">更新</button>
                  <button className="button button-quiet danger-text" disabled={history.length === 0} onClick={deleteAlertHistory} type="button">履歴を全件削除</button>
                </div>
              </div>
              {history.length === 0 ? (
                <div className="empty-state"><span>◷</span><strong>発報履歴はありません</strong><p>ルール条件が成立すると、発報イベントを SQLite に記録します。</p></div>
              ) : (
                <div className="table-scroll">
                  <table>
                    <thead>
                      <tr><th>発報日時</th><th>ルール</th><th>対象クラス</th><th>入力</th><th>モデル</th><th>動画位置</th><th>詳細</th></tr>
                    </thead>
                    <tbody>
                      {history.map((event) => (
                        <tr key={event.id}>
                          <td>{formatDate(event.created_at)}</td>
                          <td><strong>{event.rule_name}</strong></td>
                          <td><div className="class-tags">{event.detected_classes.map((name) => <span key={name}>{name}</span>)}</div></td>
                          <td>{event.source_type === 'video' ? '動画' : 'Insta360 GO Ultra'}</td>
                          <td>{event.model_name || '不明'}</td>
                          <td>{event.media_time_ms === null ? '—' : formatTime(event.media_time_ms)}</td>
                          <td><button className="text-button" onClick={() => loadAlertDetail(event.id)} type="button">表示</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
            {alertDetailLoading && !alertDetail && (
              <section className="panel alert-detail-panel">
                <p className="empty-list">詳細を読み込んでいます…</p>
              </section>
            )}
            {alertDetail && (
              <section className="panel alert-detail-panel">
                <div className="panel-heading">
                  <div><span className="eyebrow">SAVED FRAME</span><h2>{alertDetail.rule_name}</h2></div>
                  <div className="panel-heading-actions">
                    {alertDetail.frame && (
                      <a
                        className="button button-outline"
                        download={`alert-${alertDetail.id}.jpg`}
                        href={apiResourceUrl(`/alerts/${alertDetail.id}/frame`)}
                      >JPEG をダウンロード</a>
                    )}
                    <button className="icon-button" onClick={() => setAlertDetail(null)} type="button" aria-label="詳細を閉じる">×</button>
                  </div>
                </div>
                {alertDetailLoading ? (
                  <p className="empty-list">詳細を読み込んでいます…</p>
                ) : alertDetail.frame ? (
                  <div className="alert-detail-grid">
                    <div>
                      <CanvasView
                        alerts={[alertDetail]}
                        frame={alertPreview}
                        message={{ title: '保存画像を読み込めません', description: '画像 API の状態を確認してください。' }}
                        settings={{ ...settings, show_boxes: true, show_labels: true, show_confidence: true }}
                      />
                      <div className="frame-footer">
                        <span>FRAME {alertDetail.frame.frame_id}</span>
                        <span>{alertDetail.frame.detections.length} 件の検出</span>
                      </div>
                    </div>
                    <div className="alert-detail-metadata">
                      <div><span>発報日時</span><strong>{formatDate(alertDetail.created_at)}</strong></div>
                      <div><span>フレーム取得日時</span><strong>{formatDate(alertDetail.frame.captured_at)}</strong></div>
                      <div><span>入力</span><strong>{alertDetail.source_type === 'video' ? 'アップロード動画' : 'Insta360 GO Ultra'}</strong></div>
                      {alertDetail.media_time_ms !== null && (
                        <div><span>動画位置</span><strong>{formatTime(alertDetail.media_time_ms)}</strong></div>
                      )}
                      <div><span>モデル</span><strong>{alertDetail.model_name || '不明'}</strong></div>
                      <div><span>対象クラス</span><div className="class-tags">{alertDetail.detected_classes.map((name) => <span key={name}>{name}</span>)}</div></div>
                      <div>
                        <span>フレーム内の全検出</span>
                        <div className="detection-list">
                          {alertDetail.frame.detections.map((detection, index) => (
                            <div key={`${detection.class_id}-${index}`}>
                              <strong>{detection.class_name}</strong>
                              <span>{(detection.confidence * 100).toFixed(1)}%</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="empty-list">この履歴には保存画像がありません（旧バージョンで記録されたイベントです）。</p>
                )}
              </section>
            )}
          </div>
        )}

        <footer className="page-footer">
          <span>2027 つくば展示 · AI 物体検出・アラートシステム</span>
          <span>CPU ONLY <i /> LOCAL NETWORK</span>
        </footer>
      </main>
    </div>
  )
}

export default App
