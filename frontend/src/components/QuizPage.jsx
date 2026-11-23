import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  IconButton,
  LinearProgress,
  Stack,
  Typography,
} from '@mui/material'
import ArrowBackRounded from '@mui/icons-material/ArrowBackRounded'
import ArrowForwardRounded from '@mui/icons-material/ArrowForwardRounded'
import CheckCircleRounded from '@mui/icons-material/CheckCircleRounded'
import ReplayRounded from '@mui/icons-material/ReplayRounded'
import SchoolOutlined from '@mui/icons-material/SchoolOutlined'
import ZoomInRounded from '@mui/icons-material/ZoomInRounded'
import ZoomOutRounded from '@mui/icons-material/ZoomOutRounded'
import { apiRequest, apiResourceUrl } from '../services/api.js'

const clampZoom = (value) => Math.min(3, Math.max(1, value))

export default function QuizPage({ onBack }) {
  const [attempt, setAttempt] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [emptyReason, setEmptyReason] = useState('')
  const [questions, setQuestions] = useState([])
  const [questionIndex, setQuestionIndex] = useState(0)
  const [phase, setPhase] = useState('tap')
  const [imageError, setImageError] = useState(false)
  const [zoom, setZoom] = useState(1)
  const imageRef = useRef(null)
  const question = questions[questionIndex]

  useEffect(() => {
    let active = true
    apiRequest('/quiz/generate', {
      method: 'POST',
      body: JSON.stringify({ max_questions: 3 }),
    })
      .then((result) => {
        if (!active) return
        setQuestions(result.questions || [])
        setEmptyReason(result.unavailable_reason || '')
      })
      .catch((requestError) => {
        if (active) setError(requestError.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [attempt])

  const handleTargetPointer = useCallback((event) => {
    if (phase !== 'tap' || !question || !imageRef.current) return
    if (event.button !== undefined && event.button !== 0) return
    const bounds = imageRef.current.getBoundingClientRect()
    if (!bounds.width || !bounds.height) return
    const x = (event.clientX - bounds.left) * question.image_width / bounds.width
    const y = (event.clientY - bounds.top) * question.image_height / bounds.height
    const [x1, y1, x2, y2] = question.target_bbox
    if (x >= x1 && x <= x2 && y >= y1 && y <= y2) {
      event.preventDefault()
      setPhase('reason')
    }
  }, [phase, question])

  const chooseReason = () => {
    if (phase !== 'reason') return
    setPhase('explanation')
  }

  const advance = () => {
    if (questionIndex + 1 >= questions.length) {
      setPhase('complete')
      return
    }
    setQuestionIndex((current) => current + 1)
    setPhase('tap')
    setImageError(false)
    setZoom(1)
  }

  const zoomBy = (amount) => setZoom((current) => clampZoom(current + amount))

  const beginAgain = () => {
    setLoading(true)
    setError('')
    setEmptyReason('')
    setQuestions([])
    setQuestionIndex(0)
    setPhase('tap')
    setImageError(false)
    setZoom(1)
    setAttempt((current) => current + 1)
  }

  if (loading) {
    return (
      <Box sx={{ minHeight: '65vh', display: 'grid', placeItems: 'center' }}>
        <Stack spacing={2} sx={{ alignItems: 'center' }}>
          <CircularProgress aria-label="教材を読み込んでいます" />
          <Typography color="text.secondary">教材を準備しています…</Typography>
        </Stack>
      </Box>
    )
  }

  if (error) {
    return (
      <Stack spacing={3} sx={{ alignItems: 'center', py: 8 }}>
        <Alert severity="error" sx={{ width: 'min(100%, 760px)' }}>{error}</Alert>
        <Button startIcon={<ArrowBackRounded />} onClick={onBack} variant="outlined">
          ダッシュボードへ戻る
        </Button>
      </Stack>
    )
  }

  if (questions.length === 0) {
    return (
      <Stack spacing={3} sx={{ alignItems: 'center', py: 10, textAlign: 'center' }}>
        <SchoolOutlined color="primary" sx={{ fontSize: 64 }} />
        <Typography variant="h2">教材を準備中です</Typography>
        <Typography color="text.secondary" variant="body1">
          {emptyReason || '危険フレームがまだありません'}
        </Typography>
        <Button startIcon={<ArrowBackRounded />} onClick={onBack} variant="outlined">
          ダッシュボードへ戻る
        </Button>
      </Stack>
    )
  }

  if (phase === 'complete') {
    return (
      <Box className="quiz-page">
        <Card className="quiz-complete-card" elevation={1}>
          <CardContent>
            <Stack spacing={3} sx={{ alignItems: 'center', textAlign: 'center', py: { xs: 3, md: 8 } }}>
              <CheckCircleRounded color="primary" sx={{ fontSize: 88 }} />
              <Typography variant="h1">体験ありがとうございました</Typography>
              <Typography color="text.secondary" variant="body1">
                危険な箇所を見つけ、作業の安全について確認しました。
              </Typography>
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} sx={{ pt: 2 }}>
                <Button
                  onClick={beginAgain}
                  startIcon={<ReplayRounded />}
                  variant="contained"
                >
                  もう一度挑戦
                </Button>
                <Button
                  onClick={onBack}
                  startIcon={<ArrowBackRounded />}
                  variant="outlined"
                >
                  ダッシュボードに戻る
                </Button>
              </Stack>
            </Stack>
          </CardContent>
        </Card>
      </Box>
    )
  }

  const progress = ((questionIndex + 1) / questions.length) * 100
  const imageUrl = apiResourceUrl(question.image_url)
  const displayedTarget = phase !== 'tap'

  return (
    <Box className="quiz-page">
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        spacing={2}
        sx={{ alignItems: { xs: 'stretch', md: 'center' }, justifyContent: 'space-between', mb: 3 }}
      >
        <Stack direction="row" spacing={1.5} sx={{ alignItems: 'center' }}>
          <IconButton aria-label="ダッシュボードへ戻る" onClick={onBack}>
            <ArrowBackRounded />
          </IconButton>
          <Box>
            <Typography color="text.secondary" variant="overline">
              安全教育クイズ
            </Typography>
            <Typography variant="h2">危険を見つけよう</Typography>
          </Box>
        </Stack>
        <Typography color="text.secondary" variant="h3">
          {questionIndex + 1} / {questions.length} 問
        </Typography>
      </Stack>

      <LinearProgress
        aria-label="問題の進行状況"
        sx={{ height: 8, borderRadius: 8, mb: 3 }}
        value={progress}
        variant="determinate"
      />

      <Card className="quiz-card" elevation={1}>
        <CardContent sx={{ p: { xs: 2, sm: 3, lg: 4 } }}>
          {phase === 'tap' && (
            <Typography component="h2" sx={{ mb: 2 }} variant="h3">
              この画像のどこが危険でしょうか？危険だと思う場所をタップしてください。
            </Typography>
          )}
          {phase === 'reason' && (
            <Typography component="h2" sx={{ mb: 2 }} variant="h3">
              なぜ危険でしょうか？
            </Typography>
          )}
          {phase === 'explanation' && (
            <Typography component="h2" sx={{ mb: 2 }} variant="h3">
              正しい危険理由と解説
            </Typography>
          )}

          <Stack
            direction="row"
            spacing={1}
            sx={{ alignItems: 'center', justifyContent: 'flex-end', mb: 1 }}
          >
            <Typography color="text.secondary" variant="body2">画像の拡大</Typography>
            <IconButton
              aria-label="画像を縮小"
              disabled={zoom <= 1}
              onClick={() => zoomBy(-0.25)}
              size="small"
            >
              <ZoomOutRounded />
            </IconButton>
            <Typography color="text.secondary" sx={{ minWidth: 48, textAlign: 'center' }} variant="body2">
              {Math.round(zoom * 100)}%
            </Typography>
            <IconButton
              aria-label="画像を拡大"
              disabled={zoom >= 3}
              onClick={() => zoomBy(0.25)}
              size="small"
            >
              <ZoomInRounded />
            </IconButton>
          </Stack>

          <Box className="quiz-image-viewport">
            <Box
              className="quiz-image-frame"
              sx={{
                aspectRatio: `${question.image_width} / ${question.image_height}`,
                maxHeight: '68vh',
                transform: `scale(${zoom})`,
                transformOrigin: 'center center',
                width: `min(100%, ${68 * question.image_width / question.image_height}vh)`,
              }}
            >
              <Box
                alt="危険箇所をタップして回答する教材画像"
                aria-label="危険箇所をタップして回答する教材画像"
                className={`quiz-image ${phase === 'tap' ? 'is-tappable' : ''}`}
                component="img"
                draggable={false}
                onError={() => setImageError(true)}
                onLoad={() => setImageError(false)}
                onPointerDown={handleTargetPointer}
                ref={imageRef}
                src={imageUrl}
                sx={{
                  borderRadius: 2,
                  display: 'block',
                  height: '100%',
                  objectFit: 'contain',
                  touchAction: 'none',
                  userSelect: 'none',
                  width: '100%',
                }}
              />
              {displayedTarget && (
                <Box
                  aria-hidden="true"
                  className="quiz-target-overlay"
                  component="svg"
                  preserveAspectRatio="none"
                  viewBox={`0 0 ${question.image_width} ${question.image_height}`}
                  sx={{
                    height: '100%',
                    inset: 0,
                    pointerEvents: 'none',
                    position: 'absolute',
                    width: '100%',
                  }}
                >
                  <rect
                    fill="rgba(242, 184, 181, 0.24)"
                    height={question.target_bbox[3] - question.target_bbox[1]}
                    rx="4"
                    stroke="#F2B8B5"
                    strokeWidth={Math.max(3, question.image_width / 400)}
                    width={question.target_bbox[2] - question.target_bbox[0]}
                    x={question.target_bbox[0]}
                    y={question.target_bbox[1]}
                  />
                </Box>
              )}
            </Box>
          </Box>
          {imageError && (
            <Alert severity="error" sx={{ mt: 2 }}>
              教材画像を読み込めません。履歴画像の状態を確認してください。
            </Alert>
          )}

          {phase === 'reason' && (
            <Stack spacing={2} sx={{ mt: 3 }}>
              <Typography color="text.secondary" variant="body1">
                危険な理由を選んでください。
              </Typography>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={2}>
                {question.choices.map((choice, index) => (
                  <Button
                    key={choice.id}
                    onClick={chooseReason}
                    sx={{ flex: 1, justifyContent: 'flex-start', minHeight: 76, px: 3, py: 2, textAlign: 'left' }}
                    variant="outlined"
                  >
                    <Typography sx={{ whiteSpace: 'normal', textAlign: 'left' }} variant="body1">
                      {index + 1}. {choice.text}
                    </Typography>
                  </Button>
                ))}
              </Stack>
            </Stack>
          )}

          {phase === 'explanation' && (
            <Stack spacing={2} sx={{ mt: 3 }}>
              <Alert icon={<CheckCircleRounded />} severity="info">
                <Typography fontWeight={700} variant="body1">正しい危険理由</Typography>
                <Typography>{question.choices.find((choice) => choice.id === question.correct_choice_id)?.text}</Typography>
              </Alert>
              <Card variant="outlined">
                <CardContent>
                  <Typography color="text.secondary" variant="overline">解説</Typography>
                  <Typography variant="body1">{question.explanation}</Typography>
                </CardContent>
              </Card>
              <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
                <Button
                  endIcon={<ArrowForwardRounded />}
                  onClick={advance}
                  variant="contained"
                >
                  {questionIndex + 1 === questions.length ? '完了画面へ' : '次の問題'}
                </Button>
              </Box>
            </Stack>
          )}

          {phase === 'tap' && (
            <Typography color="text.secondary" sx={{ mt: 2, textAlign: 'center' }} variant="body2">
              画像を拡大して、危険だと思う場所をタップしてください。
            </Typography>
          )}
        </CardContent>
      </Card>
    </Box>
  )
}
