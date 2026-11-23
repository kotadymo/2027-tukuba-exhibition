import { createTheme } from '@mui/material/styles'
import { m3Tokens } from './tokens.js'

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: {
      main: m3Tokens.color.primary,
      contrastText: m3Tokens.color.onPrimary,
    },
    secondary: {
      main: m3Tokens.color.secondary,
      contrastText: m3Tokens.color.onSecondary,
    },
    error: {
      main: m3Tokens.color.error,
      contrastText: m3Tokens.color.onError,
    },
    background: {
      default: m3Tokens.color.background,
      paper: m3Tokens.color.surfaceContainer,
    },
    text: {
      primary: m3Tokens.color.onSurface,
      secondary: m3Tokens.color.onSurfaceVariant,
    },
    divider: m3Tokens.color.outlineVariant,
  },
  shape: {
    borderRadius: m3Tokens.shape.medium,
  },
  typography: {
    fontFamily: '"Roboto", "Noto Sans JP", "Yu Gothic UI", sans-serif',
    h1: { fontSize: '2rem', fontWeight: 600, letterSpacing: '-0.02em' },
    h2: { fontSize: '1.5rem', fontWeight: 600, letterSpacing: '-0.01em' },
    h3: { fontSize: '1.25rem', fontWeight: 600 },
    body1: { fontSize: '1rem', lineHeight: 1.65 },
    body2: { fontSize: '0.875rem', lineHeight: 1.6 },
    button: { fontSize: '0.9375rem', fontWeight: 600, textTransform: 'none' },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: {
          backgroundColor: m3Tokens.color.background,
          color: m3Tokens.color.onSurface,
        },
        '*': {
          scrollbarColor: `${m3Tokens.color.outlineVariant} transparent`,
        },
      },
    },
    MuiButton: {
      styleOverrides: {
        root: {
          minHeight: 48,
          borderRadius: m3Tokens.shape.full,
          paddingInline: 20,
          boxShadow: 'none',
        },
        contained: {
          backgroundColor: m3Tokens.color.primary,
          color: m3Tokens.color.onPrimary,
          '&:hover': {
            backgroundColor: '#B69DF2',
            boxShadow: 'none',
          },
        },
        outlined: {
          borderColor: m3Tokens.color.outline,
        },
      },
    },
    MuiIconButton: {
      styleOverrides: {
        root: {
          minWidth: 48,
          minHeight: 48,
          borderRadius: m3Tokens.shape.full,
        },
      },
    },
    MuiCard: {
      styleOverrides: {
        root: {
          borderRadius: m3Tokens.shape.extraLarge,
          backgroundImage: 'none',
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
        },
      },
    },
    MuiTextField: {
      defaultProps: {
        variant: 'outlined',
        size: 'medium',
      },
    },
  },
})
