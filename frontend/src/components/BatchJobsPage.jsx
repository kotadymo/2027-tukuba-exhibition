import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Checkbox,
  Chip,
  Divider,
  FormControlLabel,
  LinearProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import PlayArrowRounded from '@mui/icons-material/PlayArrowRounded'
import RefreshRounded from '@mui/icons-material/RefreshRounded'
import SchoolOutlined from '@mui/icons-material/SchoolOutlined'
import { apiRequest } from '../services/api.js'
import './BatchJobsPage.css'

const activeStatuses = new Set(['pending', 'running'])
const statusLabels = {
  pending: '実行待ち',
  running: '実行中',
  completed: '完了',
  completed_with_errors: '一部エラー',
  cancelled: 'キャンセル済み',
  failed: '失敗',
  interrupted: '中断',
}
const videoStatusLabels = {
  pending: '待機中',
  running: '処理中',
  completed: '完了',
  failed: '失敗',
  cancelled: 'キャンセル',
  interrupted: '中断',
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return '容量不明'
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`
}

function formatElapsed(seconds) {
  if (seconds === null || seconds === undefined) return '—'
  const total = Math.floor(seconds)
  const minutes = Math.floor(total / 60)
  return `${minutes}:${String(total % 60).padStart(2, '0')}`
}

function formatDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('ja-JP')
}

function statusColor(status) {
  if (status === 'completed') return 'success'
  if (status === 'running') return 'primary'
  if (status === 'completed_with_errors' || status === 'cancelled') return 'warning'
  if (status === 'failed' || status === 'interrupted') return 'error'
  return 'default'
}

export default function BatchJobsPage({
  activeModel,
  camera,
  playback,
  settings,
  onOpenAlerts,
  onOpenQuiz,
}) {
  const [videos, setVideos] = useState([])
  const [jobs, setJobs] = useState([])
  const [selectedIds, setSelectedIds] = useState(() => new Set())
  const [selectedJobId, setSelectedJobId] = useState(null)
  const [jobDetail, setJobDetail] = useState(null)
  const [jobAlerts, setJobAlerts] = useState([])
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [loadingVideos, setLoadingVideos] = useState(true)
  const [working, setWorking] = useState(false)

  const activeJob = useMemo(
    () => jobs.find((job) => activeStatuses.has(job.status)) || null,
    [jobs],
  )
  const selectedCount = selectedIds.size
  const liveInputActive = (
    (camera?.source_type === 'video' && playback?.status === 'playing')
    || (
      camera?.source_type === 'insta360'
      && (camera?.connect_requested || ['connecting', 'connected'].includes(camera?.status))
    )
  )

  useEffect(() => {
    let disposed = false
    apiRequest('/videos')
      .then((result) => {
        if (!disposed) setVideos(result)
      })
      .catch((requestError) => {
        if (!disposed) setError(requestError.message)
      })
      .finally(() => {
        if (!disposed) setLoadingVideos(false)
      })
    return () => {
      disposed = true
    }
  }, [])

  useEffect(() => {
    let disposed = false
    let timer

    const poll = async () => {
      try {
        const jobList = await apiRequest('/batch-jobs?limit=20&offset=0')
        const runningJob = jobList.find((job) => activeStatuses.has(job.status))
        const currentId = (
          runningJob?.id
          || (selectedJobId && jobList.some((job) => job.id === selectedJobId)
            ? selectedJobId
            : jobList[0]?.id || null)
        )
        const detail = currentId
          ? await apiRequest(`/batch-jobs/${currentId}`)
          : null
        const alerts = currentId
          ? await apiRequest(`/batch-jobs/${currentId}/alerts?limit=20&offset=0`)
          : { items: [] }
        if (!disposed) {
          setJobs(jobList)
          setSelectedJobId(currentId)
          setJobDetail(detail)
          setJobAlerts(alerts.items || [])
          setError('')
        }
      } catch (requestError) {
        if (!disposed) setError(requestError.message)
      } finally {
        if (!disposed) timer = window.setTimeout(poll, 1200)
      }
    }

    poll()
    return () => {
      disposed = true
      window.clearTimeout(timer)
    }
  }, [selectedJobId])

  const toggleVideo = (videoId) => {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(videoId)) next.delete(videoId)
      else next.add(videoId)
      return next
    })
  }

  const selectAll = () => {
    setSelectedIds(new Set(videos.map((video) => video.id)))
  }

  const clearSelection = () => setSelectedIds(new Set())

  const startJob = async () => {
    if (working || selectedCount === 0) return
    setWorking(true)
    setError('')
    setNotice('')
    try {
      const created = await apiRequest('/batch-jobs', {
        method: 'POST',
        body: JSON.stringify({ video_ids: [...selectedIds] }),
      })
      setSelectedIds(new Set())
      setSelectedJobId(created.id)
      setNotice('一括推論ジョブを開始しました。ページを移動してもBackendで処理を続けます。')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setWorking(false)
    }
  }

  const cancelJob = async () => {
    if (!activeJob || working) return
    setWorking(true)
    setError('')
    try {
      await apiRequest(`/batch-jobs/${activeJob.id}/cancel`, { method: 'POST' })
      setNotice('キャンセルを受け付けました。処理中のフレームが終わると停止します。')
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setWorking(false)
    }
  }

  const chooseJob = (jobId) => {
    setSelectedJobId(jobId)
    setError('')
  }

  const hasLoadedModel = Boolean(activeModel?.loaded && settings?.model_ready)
  const canStart = (
    selectedCount > 0
    && !activeJob
    && !working
    && hasLoadedModel
    && !liveInputActive
  )
  const progressValue = jobDetail?.progress_percent
  const currentDevice = settings?.effective_device || '未ロード'

  return (
    <div className="page-content batch-jobs-page">
      {error && <Alert severity="error" onClose={() => setError('')}>{error}</Alert>}
      {notice && <Alert severity="success" onClose={() => setNotice('')}>{notice}</Alert>}

      <Paper className="panel batch-intro-panel" elevation={0}>
        <div>
          <span className="eyebrow">OFFLINE VIDEO ANALYSIS</span>
          <Typography component="h2" variant="h5">複数の動画を順番に一括推論</Typography>
          <Typography className="batch-intro-copy">
            全フレームをリアルタイム速度に制限せず処理し、既存の危険判定・アラート保存・教材生成へ連携します。
          </Typography>
        </div>
        <Stack className="batch-runtime-summary" direction="row" useFlexGap flexWrap="wrap" spacing={1}>
          <Chip label={`モデル: ${activeModel?.name || '未選択'}`} />
          <Chip label={`実効デバイス: ${currentDevice}`} />
          <Chip label={`Confidence ${settings?.confidence ?? '—'} · IoU ${settings?.iou_threshold ?? '—'} · imgsz ${settings?.imgsz ?? '—'}`} />
        </Stack>
      </Paper>

      {activeJob && (
        <Alert severity="info" className="batch-active-notice">
          ジョブ #{activeJob.id} が{statusLabels[activeJob.status]}です。通常のライブ推論、動画再生、モデル／デバイス切替は完了まで停止されます。
        </Alert>
      )}

      <div className="batch-jobs-grid">
        <Paper className="panel batch-video-panel" elevation={0}>
          <div className="panel-heading">
            <div>
              <span className="eyebrow">REGISTERED MP4</span>
              <Typography component="h2" variant="h6">処理する動画を選択</Typography>
            </div>
            <Chip label={`${selectedCount} 件選択`} color={selectedCount ? 'primary' : 'default'} />
          </div>
          <div className="batch-selection-actions">
            <Button onClick={selectAll} disabled={videos.length === 0}>すべて選択</Button>
            <Button onClick={clearSelection} disabled={selectedCount === 0}>選択解除</Button>
          </div>
          <Divider />
          {loadingVideos ? (
            <div className="batch-empty-state"><LinearProgress /><span>登録動画を読み込んでいます…</span></div>
          ) : videos.length === 0 ? (
            <div className="batch-empty-state">
              <strong>登録済み動画がありません</strong>
              <span>ダッシュボードからMP4動画をアップロードしてください。</span>
            </div>
          ) : (
            <div className="batch-video-list">
              {videos.map((video) => (
                <FormControlLabel
                  className="batch-video-option"
                  key={video.id}
                  control={(
                    <Checkbox
                      checked={selectedIds.has(video.id)}
                      onChange={() => toggleVideo(video.id)}
                    />
                  )}
                  label={(
                    <Box className="batch-video-label">
                      <strong>{video.name}</strong>
                      <span>
                        {formatBytes(video.file_size)}
                        {video.duration_ms ? ` · ${Math.round(video.duration_ms / 1000)} 秒` : ''}
                        {video.fps ? ` · ${video.fps.toFixed(1)} FPS` : ''}
                      </span>
                    </Box>
                  )}
                />
              ))}
            </div>
          )}
          <Divider />
          <div className="batch-start-footer">
            {!hasLoadedModel && <span>開始するには有効なYOLOモデルを読み込んでください。</span>}
            {liveInputActive && <span>通常再生またはInsta360接続中は開始できません。</span>}
            {activeJob && <span>先行ジョブの終了後に開始できます。</span>}
            <Button
              className="button button-primary batch-start-button"
              disabled={!canStart}
              onClick={startJob}
              size="large"
              startIcon={<PlayArrowRounded />}
              variant="contained"
            >
              {working ? '処理中…' : '一括推論開始'}
            </Button>
          </div>
        </Paper>

        <div className="batch-results-column">
          <Paper className="panel batch-progress-panel" elevation={0}>
            <div className="panel-heading">
              <div>
                <span className="eyebrow">JOB PROGRESS</span>
                <Typography component="h2" variant="h6">
                  {jobDetail ? `ジョブ #${jobDetail.id}` : 'ジョブの進捗'}
                </Typography>
              </div>
              {jobDetail && (
                <Chip
                  color={statusColor(jobDetail.status)}
                  label={statusLabels[jobDetail.status] || jobDetail.status}
                />
              )}
            </div>
            {!jobDetail ? (
              <div className="batch-empty-state">
                <strong>まだジョブがありません</strong>
                <span>動画を選択して一括推論を開始してください。</span>
              </div>
            ) : (
              <>
                <div className="batch-progress-summary">
                  <span>{jobDetail.current_video_name ? `処理中: ${jobDetail.current_video_name}` : '現在の動画: —'}</span>
                  <strong>{jobDetail.completed_videos} / {jobDetail.total_videos} 本完了</strong>
                </div>
                <LinearProgress
                  className="batch-progress-bar"
                  value={progressValue ?? undefined}
                  variant={progressValue === null || progressValue === undefined ? 'indeterminate' : 'determinate'}
                />
                <div className="batch-stat-grid">
                  <div><span>処理フレーム</span><strong>{jobDetail.processed_frames.toLocaleString()}</strong></div>
                  <div><span>発報イベント</span><strong>{jobDetail.total_alerts.toLocaleString()}</strong></div>
                  <div><span>経過時間</span><strong>{formatElapsed(jobDetail.elapsed_seconds)}</strong></div>
                  <div><span>進捗</span><strong>{progressValue == null ? '総フレーム数不明' : `${progressValue}%`}</strong></div>
                </div>
                {jobDetail.error_message && (
                  <Alert className="batch-job-error" severity={jobDetail.status === 'failed' ? 'error' : 'warning'}>
                    <pre>{jobDetail.error_message}</pre>
                  </Alert>
                )}
                {activeStatuses.has(jobDetail.status) && (
                  <Button
                    className="button button-outline batch-cancel-button"
                    color="error"
                    disabled={working}
                    onClick={cancelJob}
                    variant="outlined"
                  >
                    {working ? 'キャンセル中…' : 'ジョブをキャンセル'}
                  </Button>
                )}
                <div className="batch-video-results">
                  {jobDetail.videos.map((video) => (
                    <div className="batch-video-result" key={video.id}>
                      <div>
                        <strong>{video.name}</strong>
                        {video.error_message && <small>{video.error_message}</small>}
                      </div>
                      <span>{video.processed_frames.toLocaleString()} フレーム · {video.alert_count} 件</span>
                      <Chip
                        color={statusColor(video.status)}
                        label={videoStatusLabels[video.status] || video.status}
                        size="small"
                      />
                    </div>
                  ))}
                </div>
                {jobDetail.status !== 'pending' && jobDetail.status !== 'running' && (
                  <Stack className="batch-result-actions" direction="row" useFlexGap flexWrap="wrap" spacing={1}>
                    <Button
                      onClick={() => onOpenAlerts(jobDetail.id)}
                      startIcon={<RefreshRounded />}
                      variant="outlined"
                    >
                      このジョブのアラート履歴
                    </Button>
                    <Button
                      onClick={onOpenQuiz}
                      startIcon={<SchoolOutlined />}
                      variant="contained"
                    >
                      生成された教材を開く
                    </Button>
                  </Stack>
                )}
                {jobAlerts.length > 0 && (
                  <div className="batch-alert-reasons">
                    <strong>発報した危険理由</strong>
                    {jobAlerts.map((alert) => (
                      <span key={alert.id}>
                        {alert.correct_reason || `理由ID ${alert.hazard_reason_id ?? '—'}`}
                      </span>
                    ))}
                    {jobDetail.total_alerts > jobAlerts.length && <small>最新20件を表示しています。</small>}
                  </div>
                )}
              </>
            )}
          </Paper>

          <Paper className="panel batch-history-panel" elevation={0}>
            <div className="panel-heading">
              <div>
                <span className="eyebrow">RECENT JOBS</span>
                <Typography component="h2" variant="h6">過去のジョブ</Typography>
              </div>
            </div>
            {jobs.length === 0 ? (
              <p className="batch-history-empty">ジョブ履歴はありません。</p>
            ) : (
              <div className="batch-history-list">
                {jobs.map((job) => (
                  <button
                    className={`batch-history-item ${job.id === selectedJobId ? 'is-selected' : ''}`}
                    key={job.id}
                    onClick={() => chooseJob(job.id)}
                    type="button"
                  >
                    <span><strong>#{job.id} · {job.model_name}</strong><small>{formatDate(job.created_at)}</small></span>
                    <Chip color={statusColor(job.status)} label={statusLabels[job.status] || job.status} size="small" />
                  </button>
                ))}
              </div>
            )}
          </Paper>
        </div>
      </div>
    </div>
  )
}
