import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button as MuiButton,
  ButtonBase,
  Checkbox as MuiCheckbox,
  FormControlLabel,
  IconButton as MuiIconButton,
  MenuItem,
  Switch as MuiSwitch,
  TextField as MuiTextField,
} from '@mui/material'
import DashboardOutlined from '@mui/icons-material/DashboardOutlined'
import DeleteOutlineRounded from '@mui/icons-material/DeleteOutlineRounded'
import EditOutlined from '@mui/icons-material/EditOutlined'
import HistoryOutlined from '@mui/icons-material/HistoryOutlined'
import Inventory2Outlined from '@mui/icons-material/Inventory2Outlined'
import MemoryOutlined from '@mui/icons-material/MemoryOutlined'
import PauseRounded from '@mui/icons-material/PauseRounded'
import PlayArrowRounded from '@mui/icons-material/PlayArrowRounded'
import ReportProblemOutlined from '@mui/icons-material/ReportProblemOutlined'
import RefreshRounded from '@mui/icons-material/RefreshRounded'
import ReplayRounded from '@mui/icons-material/ReplayRounded'
import SchoolOutlined from '@mui/icons-material/SchoolOutlined'
import SettingsOutlined from '@mui/icons-material/SettingsOutlined'
import StopRounded from '@mui/icons-material/StopRounded'
import PlaylistPlayRounded from '@mui/icons-material/PlaylistPlayRounded'
import VolumeUpOutlined from '@mui/icons-material/VolumeUpOutlined'
import { apiRequest, apiResourceUrl, liveSocketUrl, uploadFile } from './services/api.js'
import './App.css'

const QuizPage = lazy(() => import('./components/QuizPage.jsx'))
const BatchJobsPage = lazy(() => import('./components/BatchJobsPage.jsx'))

const navigation = [
  { id: 'dashboard', label: 'ダッシュボード', icon: <DashboardOutlined /> },
  { id: 'batch-jobs', label: '一括推論', icon: <PlaylistPlayRounded /> },
  { id: 'quiz', label: '問題に挑戦', icon: <SchoolOutlined /> },
  { id: 'models', label: 'モデル管理', icon: <Inventory2Outlined /> },
  { id: 'hazard-reasons', label: '危険理由マスター', icon: <ReportProblemOutlined /> },
  { id: 'settings', label: '設定', icon: <SettingsOutlined /> },
  { id: 'alerts', label: 'アラート履歴', icon: <HistoryOutlined /> },
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

function AppButton({ children, className = '', color, variant, ...props }) {
  const resolvedVariant = variant || (
    className.includes('button-primary')
      ? 'contained'
      : className.includes('button-outline')
        ? 'outlined'
        : 'text'
  )
  const resolvedColor = color || (
    className.includes('danger-text')
      ? 'error'
      : className.includes('button-primary')
        ? 'primary'
        : 'inherit'
  )
  return (
    <MuiButton
      className={className}
      color={resolvedColor}
      disableElevation
      variant={resolvedVariant}
      {...props}
    >
      {children}
    </MuiButton>
  )
}

function AppIconButton({ children, className = '', ...props }) {
  return (
    <MuiIconButton className={className} color="inherit" {...props}>
      {children}
    </MuiIconButton>
  )
}

function CanvasView({ frame, settings, alerts, message }) {
  const containerRef = useRef(null)
  const canvasRef = useRef(null)
  const drawRef = useRef(() => {})
  const drawSequenceRef = useRef(0)

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
      const sequence = ++drawSequenceRef.current
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
      context.fillStyle = '#141218'
      context.fillRect(0, 0, bounds.width, bounds.height)
      if (!frame) return

      const image = new Image()
      image.onload = () => {
        if (sequence !== drawSequenceRef.current) return
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
            ? '#f2b8b5'
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
            context.fillStyle = '#381e72'
            context.fillText(label, left + 6, labelTop + 16)
          }
        }
      }
      image.src = frame.url
    }
    drawRef.current()
    return () => {
      drawSequenceRef.current += 1
    }
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

function RuleEditor({ rule, classes, hazardReasons, onSave, onCancel, saving }) {
  const [name, setName] = useState(rule?.name || '')
  const [selectedClasses, setSelectedClasses] = useState(rule?.class_names || [])
  const [detectionDuration, setDetectionDuration] = useState(rule?.detection_duration_ms ?? 500)
  const [releaseDuration, setReleaseDuration] = useState(rule?.release_duration_ms ?? 2000)
  const [cooldown, setCooldown] = useState(rule?.cooldown_ms ?? 10000)
  const [enabled, setEnabled] = useState(rule?.enabled ?? true)
  const [educationTarget, setEducationTarget] = useState(
    rule?.education_target_class_name || '',
  )
  const [hazardReasonId, setHazardReasonId] = useState(
    rule?.hazard_reason_id ? String(rule.hazard_reason_id) : '',
  )

  const toggleClass = (className) => {
    const nextClasses = selectedClasses.includes(className)
      ? selectedClasses.filter((value) => value !== className)
      : [...selectedClasses, className]
    setSelectedClasses(nextClasses)
    if (!nextClasses.includes(educationTarget)) setEducationTarget('')
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
      education_target_class_name: educationTarget || null,
      hazard_reason_id: educationTarget && hazardReasonId
        ? Number(hazardReasonId)
        : null,
    })
  }
  const selectableHazardReasons = hazardReasons.filter(
    (reason) => reason.enabled || reason.id === rule?.hazard_reason_id,
  )
  const selectedHazardReason = selectableHazardReasons.find(
    (reason) => reason.id === Number(hazardReasonId),
  )

  return (
    <form className="rule-editor" onSubmit={submit}>
      <div className="form-heading">
        <div>
          <span className="eyebrow">{rule ? 'RULE EDITOR' : 'NEW RULE'}</span>
          <h3>{rule ? 'ルールを編集' : 'アラートルールを追加'}</h3>
        </div>
        <AppIconButton className="icon-button" type="button" onClick={onCancel} aria-label="閉じる">×</AppIconButton>
      </div>
      <MuiTextField
        fullWidth
        label="ルール名"
        onChange={(event) => setName(event.target.value)}
        placeholder="例：手とドリル"
        required
        slotProps={{ htmlInput: { maxLength: 120 } }}
        value={name}
      />
      <fieldset className="class-picker">
        <legend>必要なクラス <small>2種類以上</small></legend>
        {classes.length ? (
          <div className="class-options">
            {classes.map((item) => (
              <FormControlLabel
                className="check-option"
                control={(
                  <MuiCheckbox
                    checked={selectedClasses.includes(item.class_name)}
                    onChange={() => toggleClass(item.class_name)}
                  />
                )}
                key={item.class_id}
                label={item.class_name}
              />
            ))}
          </div>
        ) : (
          <p className="subtle-text">モデルを有効化するとクラスを選択できます。</p>
        )}
      </fieldset>
      <div className="education-rule-fields">
        <div>
          <span className="control-label">教材設定（任意）</span>
          <p className="helper-text">
            正解対象クラスと危険理由マスターを選択すると教材を作成します。
          </p>
        </div>
        <MuiTextField
          disabled={selectedClasses.length === 0}
          fullWidth
          label="問題でタップさせる正解対象クラス"
          onChange={(event) => setEducationTarget(event.target.value)}
          select
          value={selectedClasses.includes(educationTarget) ? educationTarget : ''}
        >
          <MenuItem value="">未設定</MenuItem>
          {selectedClasses.map((className) => (
            <MenuItem key={className} value={className}>{className}</MenuItem>
          ))}
        </MuiTextField>
        <MuiTextField
          disabled={!educationTarget}
          fullWidth
          label="危険理由マスター"
          onChange={(event) => setHazardReasonId(event.target.value)}
          required={Boolean(educationTarget)}
          select
          value={hazardReasonId}
        >
          <MenuItem value="">選択してください</MenuItem>
          {selectableHazardReasons.map((reason) => (
            <MenuItem key={reason.id} value={String(reason.id)}>
              {reason.name}{reason.enabled ? '' : '（無効）'}
            </MenuItem>
          ))}
        </MuiTextField>
        {selectedHazardReason && (
          <div className="reason-preview-inline">
            <span>正解: {selectedHazardReason.correct_reason}</span>
            <span>誤答: {selectedHazardReason.wrong_reason}</span>
          </div>
        )}
        {educationTarget && selectableHazardReasons.length === 0 && (
          <p className="helper-text">有効な危険理由マスターがありません。先にマスターを登録してください。</p>
        )}
      </div>
      <div className="duration-grid">
        <MuiTextField
          fullWidth
          label="成立まで (ms)"
          onChange={(event) => setDetectionDuration(event.target.value)}
          slotProps={{ htmlInput: { max: 600000, min: 0 } }}
          type="number"
          value={detectionDuration}
        />
        <MuiTextField
          fullWidth
          label="解除まで (ms)"
          onChange={(event) => setReleaseDuration(event.target.value)}
          slotProps={{ htmlInput: { max: 600000, min: 0 } }}
          type="number"
          value={releaseDuration}
        />
        <MuiTextField
          fullWidth
          label="クールダウン (ms)"
          onChange={(event) => setCooldown(event.target.value)}
          slotProps={{ htmlInput: { max: 3600000, min: 0 } }}
          type="number"
          value={cooldown}
        />
      </div>
      <FormControlLabel
        className="rule-enabled-control"
        control={<MuiSwitch checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />}
        label="このルールを有効にする"
      />
      <div className="form-actions">
        <AppButton className="button button-quiet" onClick={onCancel} type="button">キャンセル</AppButton>
        <AppButton
          className="button button-primary"
          disabled={saving || selectedClasses.length < 2 || (educationTarget && !hazardReasonId)}
          type="submit"
        >
          {saving ? '保存中…' : 'ルールを保存'}
        </AppButton>
      </div>
    </form>
  )
}

function HazardReasonEditor({ reason, onSave, onCancel, saving }) {
  const [name, setName] = useState(reason?.name || '')
  const [correctReason, setCorrectReason] = useState(reason?.correct_reason || '')
  const [wrongReason, setWrongReason] = useState(reason?.wrong_reason || '')
  const [explanation, setExplanation] = useState(reason?.explanation || '')
  const [enabled, setEnabled] = useState(reason?.enabled ?? true)

  const submit = (event) => {
    event.preventDefault()
    onSave({
      name,
      correct_reason: correctReason,
      wrong_reason: wrongReason,
      explanation,
      enabled,
    })
  }

  return (
    <form className="rule-editor hazard-reason-editor" onSubmit={submit}>
      <div className="form-heading">
        <div>
          <span className="eyebrow">{reason ? 'HAZARD REASON EDITOR' : 'NEW HAZARD REASON'}</span>
          <h3>{reason ? '危険理由を編集' : '危険理由マスターを追加'}</h3>
        </div>
        <AppIconButton className="icon-button" type="button" onClick={onCancel} aria-label="閉じる">×</AppIconButton>
      </div>
      <MuiTextField
        fullWidth
        label="名称"
        onChange={(event) => setName(event.target.value)}
        required
        slotProps={{ htmlInput: { maxLength: 160 } }}
        value={name}
      />
      <MuiTextField
        fullWidth
        label="正しい危険理由"
        maxRows={4}
        minRows={2}
        onChange={(event) => setCorrectReason(event.target.value)}
        required
        slotProps={{ htmlInput: { maxLength: 2000 } }}
        value={correctReason}
        multiline
      />
      <MuiTextField
        fullWidth
        label="誤答として使用する理由"
        maxRows={4}
        minRows={2}
        onChange={(event) => setWrongReason(event.target.value)}
        required
        slotProps={{ htmlInput: { maxLength: 2000 } }}
        value={wrongReason}
        multiline
      />
      <MuiTextField
        fullWidth
        label="正解後に表示する解説"
        maxRows={5}
        minRows={2}
        onChange={(event) => setExplanation(event.target.value)}
        required
        slotProps={{ htmlInput: { maxLength: 4000 } }}
        value={explanation}
        multiline
      />
      <div className="reason-preview">
        <span className="eyebrow">2択プレビュー</span>
        <div className="reason-preview-choice"><strong>選択肢 A</strong><span>{correctReason || '正解理由を入力してください'}</span></div>
        <div className="reason-preview-choice"><strong>選択肢 B</strong><span>{wrongReason || '誤答理由を入力してください'}</span></div>
        <p>{explanation || '解説を入力してください'}</p>
        <small>実際の問題では選択肢の表示順が出題時にランダム化されます。</small>
      </div>
      <FormControlLabel
        className="rule-enabled-control"
        control={<MuiSwitch checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />}
        label="この危険理由を有効にする"
      />
      <div className="form-actions">
        <AppButton className="button button-quiet" onClick={onCancel} type="button">キャンセル</AppButton>
        <AppButton className="button button-primary" disabled={saving} type="submit">
          {saving ? '保存中…' : '危険理由を保存'}
        </AppButton>
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
  const [hazardReasons, setHazardReasons] = useState([])
  const [history, setHistory] = useState([])
  const [batchJobs, setBatchJobs] = useState([])
  const [alertFilterJobId, setAlertFilterJobId] = useState(null)
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
  const [editingHazardReason, setEditingHazardReason] = useState(undefined)
  const [savingHazardReason, setSavingHazardReason] = useState(false)
  const [insta360Draft, setInsta360Draft] = useState(null)
  const [settingsDraft, setSettingsDraft] = useState(null)
  const [savingSettings, setSavingSettings] = useState(false)
  const frameRef = useRef(null)
  const cameraSourceRef = useRef(null)
  const audioContextRef = useRef(null)
  const audioUnlockedRef = useRef(false)
  const settingsRef = useRef(null)
  const alertFilterJobRef = useRef(null)
  const playSoundRef = useRef(() => {})
  const testAlertTimeoutRef = useRef(null)

  const refreshData = useCallback(async (batchJobId = alertFilterJobRef.current) => {
    try {
      const [healthResult, sourceResult, insta360Result, settingsResult, modelsResult, activeModelResult,
        videosResult, playbackResult, rulesResult, hazardReasonsResult, historyResult, batchJobsResult] = await Promise.all([
        apiRequest('/health'),
        apiRequest('/source'),
        apiRequest('/insta360'),
        apiRequest('/settings'),
        apiRequest('/models'),
        apiRequest('/models/active'),
        apiRequest('/videos'),
        apiRequest('/videos/playback'),
        apiRequest('/rules'),
        apiRequest('/hazard-reasons'),
        apiRequest(batchJobId
          ? `/alerts?limit=50&offset=0&batch_job_id=${batchJobId}`
          : '/alerts?limit=50&offset=0'),
        apiRequest('/batch-jobs?limit=20&offset=0'),
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
      setHazardReasons(hazardReasonsResult)
      setHistory(historyResult.items || [])
      setBatchJobs(batchJobsResult)
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
    alertFilterJobRef.current = alertFilterJobId
  }, [alertFilterJobId])

  useEffect(() => {
    let disposed = false
    let timer
    const pollBatchJobs = async () => {
      try {
        const result = await apiRequest('/batch-jobs?limit=20&offset=0')
        if (!disposed) setBatchJobs(result)
      } catch (error) {
        if (!disposed) setApiError(error.message)
      } finally {
        if (!disposed) timer = window.setTimeout(pollBatchJobs, 1500)
      }
    }
    pollBatchJobs()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [])

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
        if (event.test) {
          window.clearTimeout(testAlertTimeoutRef.current)
          testAlertTimeoutRef.current = window.setTimeout(() => {
            setActiveAlerts((current) => current.filter((item) => item.id !== alert.id))
            testAlertTimeoutRef.current = null
          }, 4000)
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
      } else if (event.type === 'device_changed' || event.type === 'device_fallback') {
        clearCurrentFrame()
        setActiveAlerts([])
        setSettings((current) => current ? { ...current, ...event } : current)
        setSettingsDraft((current) => current
          ? { ...current, requested_device: event.requested_device || current.requested_device }
          : current)
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
        setSettings((current) => current ? {
          ...current,
          inference_fps: metadata.inference_fps,
          processing_ms: metadata.processing_ms,
          effective_device: metadata.effective_device || current.effective_device,
        } : current)
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

  useEffect(() => () => {
    window.clearTimeout(testAlertTimeoutRef.current)
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
  const detectionFps = frame?.meta.inference_fps ?? settings?.inference_fps ?? 0
  const processingMs = frame?.meta.processing_ms ?? settings?.processing_ms
  const currentVideo = videos.find((video) => video.id === camera?.active_video_id)
  const latestAlert = activeAlerts.at(-1)
  const alertCount = history.length
  const activeBatchJob = batchJobs.find(
    (job) => job.status === 'pending' || job.status === 'running',
  ) || null
  const batchJobActive = activeBatchJob !== null

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

  const saveHazardReason = async (payload) => {
    setSavingHazardReason(true)
    try {
      const reasonId = editingHazardReason?.id
      await apiRequest(reasonId ? `/hazard-reasons/${reasonId}` : '/hazard-reasons', {
        method: reasonId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      })
      setEditingHazardReason(undefined)
      await refreshData()
    } catch (error) {
      setApiError(error.message)
    } finally {
      setSavingHazardReason(false)
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
        education_target_class_name: rule.education_target_class_name || null,
        hazard_reason_id: rule.hazard_reason_id || null,
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

  const toggleHazardReason = (reason) => handleAction(async () => {
    await apiRequest(`/hazard-reasons/${reason.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: reason.name,
        correct_reason: reason.correct_reason,
        wrong_reason: reason.wrong_reason,
        explanation: reason.explanation,
        enabled: !reason.enabled,
      }),
    })
    await refreshData()
  })

  const deleteHazardReason = (reason) => {
    if (reason.reference_rule_count > 0) return
    if (!window.confirm(`「${reason.name}」を削除しますか？`)) return
    handleAction(async () => {
      await apiRequest(`/hazard-reasons/${reason.id}`, { method: 'DELETE' })
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
        const { requested_device, confidence, iou_threshold, imgsz, target_fps, jpeg_quality, show_boxes, show_labels,
          show_confidence, audio_enabled } = settingsDraft
        const result = await apiRequest('/settings', {
          method: 'PATCH',
          body: JSON.stringify({
            requested_device,
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
      } catch (error) {
        await refreshData().catch(() => {})
        throw error
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
    if (alertFilterJobId !== null) return
    if (!window.confirm('すべてのアラート履歴と保存画像を削除しますか？この操作は取り消せません。')) return
    handleAction(async () => {
      await apiRequest('/alerts', { method: 'DELETE' })
      setAlertDetail(null)
      await refreshData()
    })
  }

  const openBatchJobAlerts = async (jobId) => {
    setAlertFilterJobId(jobId)
    setAlertDetail(null)
    setPage('alerts')
    try {
      await refreshData(jobId)
    } catch (error) {
      setApiError(error.message)
    }
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
      if (playback?.status === 'ready') return { title: '再生待機中', description: '再生ボタンを押すと推論を開始します' }
    } else {
      if (camera?.status === 'error') return { title: 'カメラに接続できません', description: camera.insta360_error || 'GO Ultra の Wi-Fi 接続と設定を確認してください' }
      if (camera?.status !== 'connected') return { title: 'カメラ未接続', description: '展示 PC を GO Ultra の Wi-Fi に接続し、接続を開始してください' }
    }
    if (staleFrame) return { title: '推論フレームが届いていません', description: '入力ソースまたは推論デバイスの状態を確認してください' }
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
            <ButtonBase
              className={`nav-item ${page === item.id ? 'is-active' : ''}`}
              key={item.id}
              onClick={() => {
                if (item.id === 'alerts') {
                  setAlertFilterJobId(null)
                  refreshData(null).catch(() => {})
                }
                setPage(item.id)
              }}
              type="button"
            >
              <span className="nav-icon">{item.icon}</span>
              <span>{item.label}</span>
              {item.id === 'batch-jobs' && batchJobActive && <span className="nav-count batch-nav-count">進行</span>}
              {item.id === 'alerts' && alertCount > 0 && <span className="nav-count">{alertCount}</span>}
            </ButtonBase>
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
          <div className="sidebar-version">展示用システム <span>v0.7.0</span></div>
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
            {batchJobActive && (
              <div className="batch-job-pill" role="status">
                一括推論 #{activeBatchJob.id} · {activeBatchJob.status === 'running' ? '実行中' : '開始待ち'}
              </div>
            )}
            {page !== 'quiz' && (
              <AppButton
                onClick={() => setPage('quiz')}
                startIcon={<SchoolOutlined />}
                variant="contained"
              >
                問題に挑戦
              </AppButton>
            )}
            <AppIconButton
              aria-label="再読み込み"
              className="icon-button refresh-button"
              onClick={() => refreshData().catch(() => {})}
              title="再読み込み"
              type="button"
            >
              <RefreshRounded />
            </AppIconButton>
          </div>
        </header>

        {apiError && (
          <div className="notice notice-error" role="alert">
            <span>!</span>
            <p>{apiError}</p>
            <AppIconButton className="icon-button" onClick={() => setApiError('')} type="button" aria-label="閉じる">×</AppIconButton>
          </div>
        )}
        {alertSaveError && (
          <div className="notice notice-error" role="alert">
            <span>!</span>
            <p>{alertSaveError}</p>
            <AppIconButton className="icon-button" onClick={() => setAlertSaveError('')} type="button" aria-label="閉じる">×</AppIconButton>
          </div>
        )}

        {page !== 'quiz' && activeAlerts.length > 0 && (
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
                <AppIconButton
                  className="icon-button"
                onClick={() => {
                  if (alert.test) {
                    window.clearTimeout(testAlertTimeoutRef.current)
                    testAlertTimeoutRef.current = null
                  }
                  setActiveAlerts((current) => current.filter((item) => item.id !== alert.id))
                }}
                  type="button"
                  aria-label="警告を閉じる"
                >×</AppIconButton>
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
                  <i />{activeModel?.loaded
                    ? `${settings?.effective_device || '推論'} 推論準備完了`
                    : 'モデル待機中'}
                </span>
              </div>
              <div className="metric-card">
                <span className="metric-label">推論 FPS</span>
                <strong>{detectionFps.toFixed(1)}<small> FPS</small></strong>
                <span className="metric-caption">目標 {settings?.target_fps ?? 5} FPS</span>
              </div>
              <div className="metric-card">
                <span className="metric-label">処理時間</span>
                <strong>{processingMs === undefined || processingMs === null ? '--' : processingMs.toFixed(1)}<small> ms</small></strong>
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
                      <ButtonBase
                        className={camera?.source_type === 'insta360' ? 'is-selected' : ''}
                        disabled={batchJobActive}
                        onClick={() => changeSource('insta360')}
                        type="button"
                      >Insta360 GO Ultra（ライブ）</ButtonBase>
                      <ButtonBase
                        className={camera?.source_type === 'video' ? 'is-selected' : ''}
                        disabled={batchJobActive}
                        onClick={() => changeSource('video')}
                        type="button"
                      >アップロード動画</ButtonBase>
                    </div>
                  </div>
                  {camera?.source_type === 'video' ? (
                    <div className="media-controls">
                      <div className="media-control-buttons">
                        <AppButton className="button button-primary" disabled={!currentVideo || batchJobActive} onClick={() => playbackAction('play')} startIcon={<PlayArrowRounded />} type="button">
                          再生
                        </AppButton>
                        <AppButton className="button button-quiet" disabled={!currentVideo || batchJobActive} onClick={() => playbackAction('pause')} startIcon={<PauseRounded />} type="button">
                          一時停止
                        </AppButton>
                        <AppButton className="button button-quiet" disabled={!currentVideo || batchJobActive} onClick={() => playbackAction('restart')} startIcon={<ReplayRounded />} type="button">
                          先頭から
                        </AppButton>
                        <AppButton className="button button-quiet" disabled={!currentVideo || batchJobActive} onClick={() => playbackAction('stop')} startIcon={<StopRounded />} type="button">
                          停止
                        </AppButton>
                      </div>
                      <FormControlLabel
                        className="toggle-row loop-toggle"
                        control={(
                          <MuiSwitch
                            checked={playback?.loop ?? true}
                            disabled={batchJobActive}
                            onChange={(event) => setLoop(event.target.checked)}
                          />
                        )}
                        label="ループ再生"
                      />
                    </div>
                  ) : (
                    <div className="camera-controls">
                      <span className="source-url">
                        {insta360?.camera_ip}:{insta360?.tcp_port}
                        {insta360?.error ? ` · ${insta360.error}` : ''}
                      </span>
                      {camera?.status === 'connected' || camera?.status === 'connecting' ? (
                        <AppButton className="button button-quiet" disabled={batchJobActive} onClick={disconnectInsta360} type="button">切断</AppButton>
                      ) : (
                        <AppButton className="button button-primary" disabled={batchJobActive} onClick={connectInsta360} type="button">接続開始</AppButton>
                      )}
                    </div>
                  )}
                  {camera?.source_type === 'video' && (
                    <div className="video-library-controls">
                      <MuiTextField
                        className="video-select"
                        disabled={batchJobActive}
                        label="登録済み動画を選択"
                        onChange={selectVideo}
                        select
                        value={camera?.active_video_id || ''}
                      >
                        <MenuItem value="">動画を選択…</MenuItem>
                        {videos.map((video) => (
                          <MenuItem key={video.id} value={video.id}>
                            {video.name} · {formatTime(video.duration_ms)}
                          </MenuItem>
                        ))}
                      </MuiTextField>
                      <label className="button button-outline upload-button">
                        <input accept=".mp4,video/mp4" disabled={batchJobActive} onChange={handleVideoUpload} type="file" />
                        ＋ MP4 を追加
                      </label>
                    </div>
                  )}
                  {camera?.source_type === 'video' && currentVideo && (
                    <div className="video-details">
                      <span>{currentVideo.width && currentVideo.height ? `${currentVideo.width} × ${currentVideo.height}` : '解像度不明'}</span>
                      <span>{currentVideo.fps ? `${currentVideo.fps.toFixed(2)} FPS` : 'FPS 不明'}</span>
                      <span>{formatBytes(currentVideo.file_size)}</span>
                      <AppButton
                        className="text-button danger-text"
                        disabled={playback?.status === 'playing' || batchJobActive}
                        onClick={() => removeVideo(currentVideo.id)}
                        type="button"
                        startIcon={<DeleteOutlineRounded />}
                      >動画を削除</AppButton>
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
                    <AppButton className="button button-outline" onClick={audioUnlocked ? toggleAudio : unlockAudio} startIcon={<VolumeUpOutlined />} type="button">
                      {audioUnlocked ? `音声 ${settings?.audio_enabled ? 'ON' : 'OFF'}` : '♫ 音声を有効化'}
                    </AppButton>
                    <AppButton className="button button-quiet" onClick={testAlert} type="button">テスト発報</AppButton>
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
                    <AppButton className="button button-small button-outline" onClick={() => setEditingRule(null)} type="button">
                      ＋ 追加
                    </AppButton>
                  </div>
                  {editingRule !== undefined && (
                    <RuleEditor
                      classes={activeModel?.classes || []}
                      hazardReasons={hazardReasons}
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
                    {rules
                      .filter((rule) => rule.id !== editingRule?.id)
                      .map((rule) => (
                      <article className={`rule-item ${rule.incompatible ? 'is-incompatible' : ''}`} key={rule.id}>
                        <div className="rule-main">
                          <MuiSwitch
                            checked={rule.enabled}
                            slotProps={{ input: { 'aria-label': rule.enabled ? 'ルールを無効化' : 'ルールを有効化' } }}
                            onChange={() => toggleRule(rule)}
                            size="small"
                          />
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
                            <AppButton className="text-button" onClick={() => setEditingRule(rule)} startIcon={<EditOutlined />} type="button">編集</AppButton>
                            <AppButton className="text-button danger-text" onClick={() => deleteRule(rule)} startIcon={<DeleteOutlineRounded />} type="button">削除</AppButton>
                          </div>
                        </div>
                      </article>
                    ))}
                  </div>
                </section>

                <section className="panel recent-panel">
                  <div className="panel-heading">
                    <div><span className="eyebrow">RECENT EVENTS</span><h2>最近の発報</h2></div>
                    <AppButton
                      className="text-button"
                      onClick={() => {
                        setAlertFilterJobId(null)
                        refreshData(null).catch(() => {})
                        setPage('alerts')
                      }}
                      type="button"
                    >すべて見る →</AppButton>
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

        {page === 'quiz' && (
          <Suspense fallback={<div className="empty-list">教材画面を読み込んでいます…</div>}>
            <QuizPage onBack={() => setPage('dashboard')} />
          </Suspense>
        )}

        {page === 'batch-jobs' && (
          <Suspense fallback={<div className="empty-list">一括推論画面を読み込んでいます…</div>}>
            <BatchJobsPage
              activeModel={activeModel}
              camera={camera}
              onOpenAlerts={openBatchJobAlerts}
              onOpenQuiz={() => setPage('quiz')}
              playback={playback}
              settings={settings}
            />
          </Suspense>
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
                          <AppButton className="button button-primary" disabled={batchJobActive} onClick={() => activateModel(model)} type="button">
                            このモデルを有効化
                          </AppButton>
                        )}
                        <AppButton
                          className="text-button danger-text"
                          disabled={model.active || batchJobActive}
                          onClick={() => removeModel(model)}
                          type="button"
                          startIcon={<DeleteOutlineRounded />}
                        >削除</AppButton>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {page === 'hazard-reasons' && (
          <div className="page-content narrow-page hazard-reasons-page">
            <section className="panel">
              <div className="panel-heading">
                <div>
                  <span className="eyebrow">HAZARD REASON MASTER</span>
                  <h2>危険理由マスター <span className="heading-count">{hazardReasons.length}</span></h2>
                </div>
                {editingHazardReason === undefined && (
                  <AppButton
                    className="button button-primary"
                    onClick={() => setEditingHazardReason(null)}
                    type="button"
                  >＋ 新規登録</AppButton>
                )}
              </div>
              <p className="section-description">
                正解・誤答・解説を一組で管理し、複数のアラートルールから共有できます。
                登録した内容は発報時に教材へスナップショット保存されます。
              </p>
              {editingHazardReason !== undefined && (
                <HazardReasonEditor
                  key={editingHazardReason?.id || 'new-hazard-reason'}
                  onCancel={() => setEditingHazardReason(undefined)}
                  onSave={saveHazardReason}
                  reason={editingHazardReason}
                  saving={savingHazardReason}
                />
              )}
              {hazardReasons.length === 0 ? (
                <div className="empty-state">
                  <ReportProblemOutlined />
                  <strong>危険理由マスターがありません</strong>
                  <p>新規登録してから、アラートルールへ割り当ててください。</p>
                </div>
              ) : (
                <div className="hazard-reason-list">
                  {hazardReasons.map((reason) => (
                    <article
                      className={`hazard-reason-card ${reason.enabled ? '' : 'is-disabled'}`}
                      key={reason.id}
                    >
                      <div className="hazard-reason-heading">
                        <div>
                          <h3>{reason.name}</h3>
                          <span className="reference-count">
                            {reason.reference_rule_count} 件のルールから参照中
                          </span>
                        </div>
                        <div className="hazard-reason-actions">
                          <FormControlLabel
                            className="reason-enabled-control"
                            control={(
                              <MuiSwitch
                                checked={reason.enabled}
                                onChange={() => toggleHazardReason(reason)}
                              />
                            )}
                            label={reason.enabled ? '有効' : '無効'}
                          />
                          <AppButton
                            className="text-button"
                            onClick={() => setEditingHazardReason(reason)}
                            startIcon={<EditOutlined />}
                            type="button"
                          >編集</AppButton>
                          <AppButton
                            className="text-button danger-text"
                            disabled={reason.reference_rule_count > 0}
                            onClick={() => deleteHazardReason(reason)}
                            startIcon={<DeleteOutlineRounded />}
                            type="button"
                          >削除</AppButton>
                        </div>
                      </div>
                      <div className="reason-preview-pair">
                        <div><strong>正解理由</strong><span>{reason.correct_reason}</span></div>
                        <div><strong>誤答</strong><span>{reason.wrong_reason}</span></div>
                      </div>
                      <p className="hazard-reason-explanation">{reason.explanation}</p>
                    </article>
                  ))}
                </div>
              )}
            </section>
          </div>
        )}

        {page === 'settings' && (
          <div className="page-content narrow-page settings-page">
            <section className="panel inference-device-panel">
              <div className="section-title">
                <div className="section-icon"><MemoryOutlined /></div>
                <div><span className="eyebrow">INFERENCE DEVICE</span><h2>GPU / CPU 推論デバイス</h2></div>
              </div>
              <div className="settings-grid device-setting-grid">
                <MuiTextField
                  fullWidth
                  disabled={batchJobActive}
                  helperText={batchJobActive
                    ? '一括推論ジョブ終了後にデバイスを変更できます。'
                    : 'Auto は CUDA を優先し、利用できない場合は CPU を使います。'}
                  label="要求する推論モード"
                  onChange={(event) => changeSetting('requested_device', event.target.value)}
                  select
                  value={settingsDraft?.requested_device || 'auto'}
                >
                  <MenuItem value="auto">Auto（CUDA優先・CPUフォールバック）</MenuItem>
                  <MenuItem value="cuda">NVIDIA GPU（CUDA固定）</MenuItem>
                  <MenuItem value="cpu">CPU固定</MenuItem>
                </MuiTextField>
                <div className="device-status-grid">
                  <div><span>要求設定</span><strong>{settings?.requested_device || 'auto'}</strong></div>
                  <div><span>実効デバイス</span><strong>{settings?.effective_device || 'none'}</strong></div>
                  <div><span>CUDA 利用可否</span><strong>{settings?.cuda_available ? '利用可能' : '利用不可'}</strong></div>
                  <div><span>NVIDIA GPU</span><strong>{settings?.gpu_name || '検出されていません'}</strong></div>
                  <div><span>推論 FPS</span><strong>{Number(settings?.inference_fps || 0).toFixed(1)} FPS</strong></div>
                  <div><span>推論処理時間</span><strong>{settings?.processing_ms == null ? '—' : `${Number(settings.processing_ms).toFixed(1)} ms`}</strong></div>
                </div>
              </div>
              {settings?.fallback_reason && (
                <div className="notice notice-warning device-notice">
                  <span>!</span>
                  <p>
                    {settings.effective_device === 'cpu'
                      ? 'CPU フォールバック中: '
                      : 'CUDAを利用できないためAutoはCPUを選択します: '}
                    {settings.fallback_reason}
                  </p>
                </div>
              )}
              {settings?.device_error && (
                <div className="notice notice-error device-notice">
                  <span>!</span><p>{settings.device_error}</p>
                </div>
              )}
              <p className="helper-text">デバイスモードは下の「設定を保存」を押すと適用されます。切替時は推論を一時停止してモデルを再ロードします。</p>
              {!settings?.cuda_available && settings?.cuda_error && (
                <p className="helper-text">{settings.cuda_error}</p>
              )}
            </section>

            <section className="panel">
              <div className="section-title">
                <div className="section-icon">⌁</div>
                <div><span className="eyebrow">INSTA360 GO ULTRA</span><h2>ライブ入力設定</h2></div>
              </div>
              <form className="settings-form" onSubmit={saveInsta360}>
                <div className="settings-grid">
                  <MuiTextField
                    fullWidth
                    label="カメラ IPv4 アドレス"
                    onChange={(event) => setInsta360Draft((current) => ({ ...current, camera_ip: event.target.value }))}
                    placeholder="192.168.42.1"
                    value={insta360Draft?.camera_ip ?? '192.168.42.1'}
                  />
                  <MuiTextField
                    fullWidth
                    label="TCP ポート"
                    onChange={(event) => setInsta360Draft((current) => ({ ...current, tcp_port: Number(event.target.value) }))}
                    slotProps={{ htmlInput: { max: 65535, min: 1 } }}
                    type="number"
                    value={insta360Draft?.tcp_port ?? 6666}
                  />
                  <MuiTextField
                    fullWidth
                    label="再接続間隔 (ms)"
                    onChange={(event) => setInsta360Draft((current) => ({ ...current, reconnect_interval_ms: Number(event.target.value) }))}
                    slotProps={{ htmlInput: { max: 60000, min: 500 } }}
                    type="number"
                    value={insta360Draft?.reconnect_interval_ms ?? 3000}
                  />
                </div>
                <p className="helper-text">GO Ultra の Wi-Fi には OS 側で接続してください。接続・切断はダッシュボードから操作できます。</p>
                <div className="form-actions">
                  <AppButton className="button button-primary" disabled={batchJobActive} type="submit">Insta360 設定を保存</AppButton>
                  <span className={`state-chip ${statusClass(insta360?.status)}`}>
                    <i />{statusNames[insta360?.status] || '未接続'}
                  </span>
                </div>
              </form>
            </section>

            <section className="panel">
              <div className="section-title">
                <div className="section-icon"><MemoryOutlined /></div>
                <div><span className="eyebrow">MODEL INFERENCE</span><h2>推論・表示設定</h2></div>
              </div>
              <form className="settings-form" id="inference-settings-form" onSubmit={saveSettings}>
                <div className="settings-grid">
                  <MuiTextField
                    fullWidth
                    helperText="信頼度の下限 (0〜1)"
                    label="Confidence"
                    onChange={(event) => changeInferenceNumber('confidence', event.target.value)}
                    slotProps={{ htmlInput: { max: 1, min: 0, step: 0.01 } }}
                    type="number"
                    value={settingsDraft?.confidence ?? 0.4}
                  />
                  <MuiTextField
                    fullWidth
                    helperText="重複ボックス抑制 (0〜1)"
                    label="NMS IoU"
                    onChange={(event) => changeInferenceNumber('iou_threshold', event.target.value)}
                    slotProps={{ htmlInput: { max: 1, min: 0, step: 0.01 } }}
                    type="number"
                    value={settingsDraft?.iou_threshold ?? 0.7}
                  />
                  <MuiTextField
                    fullWidth
                    helperText="モデル互換の整数値"
                    label="画像サイズ (imgsz)"
                    onChange={(event) => changeInferenceNumber('imgsz', event.target.value)}
                    slotProps={{ htmlInput: { max: 2048, min: 32, step: 32 } }}
                    type="number"
                    value={settingsDraft?.imgsz ?? 416}
                  />
                  <MuiTextField
                    fullWidth
                    helperText="上限目標。性能保証値ではありません。"
                    label="目標推論 FPS"
                    onChange={(event) => changeInferenceNumber('target_fps', event.target.value)}
                    slotProps={{ htmlInput: { max: 60, min: 0.1, step: 0.5 } }}
                    type="number"
                    value={settingsDraft?.target_fps ?? 5}
                  />
                  <MuiTextField
                    fullWidth
                    helperText="履歴に保存する画像の品質 (1〜100)"
                    label="アラート画像 JPEG 品質"
                    onChange={(event) => changeInferenceNumber('jpeg_quality', event.target.value)}
                    slotProps={{ htmlInput: { max: 100, min: 1, step: 1 } }}
                    type="number"
                    value={settingsDraft?.jpeg_quality ?? 80}
                  />
                </div>
                <div className="display-options">
                  <span className="control-label">映像表示</span>
                  {[
                    ['show_boxes', 'Bounding Box'],
                    ['show_labels', 'クラス名'],
                    ['show_confidence', 'Confidence'],
                  ].map(([key, label]) => (
                    <FormControlLabel
                      className="check-option"
                      control={(
                        <MuiCheckbox
                          checked={settingsDraft?.[key] ?? true}
                          onChange={(event) => changeSetting(key, event.target.checked)}
                        />
                      )}
                      key={key}
                      label={label}
                    />
                  ))}
                </div>
                <div className="settings-footer">
                  <FormControlLabel
                    className="toggle-row"
                    control={(
                      <MuiSwitch
                        checked={settingsDraft?.audio_enabled ?? true}
                        onChange={(event) => changeSetting('audio_enabled', event.target.checked)}
                      />
                    )}
                    label={(
                      <span>
                        <strong>ブラウザ警告音</strong>
                        <small>音声の有効化はダッシュボードから操作します</small>
                      </span>
                    )}
                  />
                  <AppButton className="button button-primary" disabled={savingSettings} type="submit">
                    {savingSettings ? '保存中…' : '設定を保存'}
                  </AppButton>
                </div>
              </form>
            </section>

            <section className="panel runtime-panel">
              <div><span className="eyebrow">RUNTIME</span><h2>実行環境</h2></div>
              <div className="runtime-grid">
                <span>推論デバイス</span><strong>{settings?.effective_device || 'none'}</strong>
                <span>要求モード</span><strong>{settings?.requested_device || 'auto'}</strong>
                <span>CUDA</span><strong>{settings?.cuda_available ? '利用可能' : '利用不可'}</strong>
                <span>GPU</span><strong>{settings?.gpu_name || '検出されていません'}</strong>
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
                <div>
                  <span className="eyebrow">EVENT LOG</span>
                  <h2>{alertFilterJobId ? `ジョブ #${alertFilterJobId} のアラート履歴` : 'アラート履歴'}</h2>
                </div>
                <div className="panel-heading-actions">
                  <AppButton className="button button-outline" onClick={() => refreshData(alertFilterJobId).catch(() => {})} startIcon={<RefreshRounded />} type="button">更新</AppButton>
                  {alertFilterJobId !== null ? (
                    <AppButton
                      className="button button-quiet"
                      onClick={() => {
                        setAlertFilterJobId(null)
                        refreshData(null).catch(() => {})
                      }}
                      type="button"
                    >全履歴を表示</AppButton>
                  ) : (
                    <AppButton className="button button-quiet danger-text" disabled={history.length === 0} onClick={deleteAlertHistory} startIcon={<DeleteOutlineRounded />} type="button">履歴を全件削除</AppButton>
                  )}
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
                          <td><AppButton className="text-button" onClick={() => loadAlertDetail(event.id)} type="button">表示</AppButton></td>
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
                    <AppIconButton className="icon-button" onClick={() => setAlertDetail(null)} type="button" aria-label="詳細を閉じる">×</AppIconButton>
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
