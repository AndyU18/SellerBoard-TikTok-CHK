// Tipos y llamadas de los datos de TikTok orgánico (backend: app/tiktok_organic_api.py y
// app/tiktok_organic_analytics.py). Solo lectura (GET, permiso profit.view). La conexión vive en lib/tiktokOrganic.ts.
import { authFetch, parse, query } from '@/lib/api'

const BASE = '/api/tiktok-organic'

/** Criterios de orden que acepta /videos. */
export type OrganicSort = 'recent' | 'views' | 'likes' | 'comments' | 'shares' | 'engagement' | 'views_per_day'
/** Máximo de videos por página que acepta /videos. */
export const VIDEOS_MAX_LIMIT = 200

/** Cambio entre la primera y la última foto diaria dentro de una ventana (null si hay menos de dos fotos). */
export interface OrganicGrowth {
  days: number
  from_date: string
  to_date: string
  delta: number
  pct: number | null
}

export interface OrganicAccount {
  username: string | null
  display_name: string | null
  avatar_url: string | null
  bio: string | null
  profile_url: string | null
  is_verified: boolean | null
  snapshot_date: string
}

export interface OrganicOverview {
  has_data: boolean
  account: OrganicAccount | null
  followers: number | null
  following: number | null
  likes_total: number | null
  videos_reported: number | null
  growth: { days: number; followers: OrganicGrowth | null }[]
  videos_tracked: number
  total_views: number
  total_likes: number
  total_comments: number
  total_shares: number
  engagement: number | null
  avg_views: number | null
  median_views: number | null
  posting: {
    days: number
    videos_published: number
    views: number
    posts_per_week: number
    last_posted_at: string | null
    days_since_last_post: number | null
  }
}

export interface OrganicVideo {
  video_id: string
  title: string | null
  description: string | null
  cover_url: string | null
  share_url: string | null
  embed_link: string | null
  created_at: string | null
  duration_seconds: number | null
  width: number | null
  height: number | null
  views: number
  likes: number
  comments: number
  shares: number
  engagement: number | null
  views_per_day: number
  age_days: number | null
}

export interface OrganicVideoListItem extends OrganicVideo {
  recent_growth: OrganicGrowth | null
}

export interface OrganicVideosResponse {
  total: number
  sort: OrganicSort
  q: string | null
  limit: number
  offset: number
  items: OrganicVideoListItem[]
}

export interface FollowersPoint {
  date: string
  followers: number
}

export interface ViewsPoint {
  date: string
  views_total: number
  /** Vistas ganadas desde la foto anterior; null en el primer día. */
  views_gained: number | null
}

export interface ViralVideo extends OrganicVideo {
  times_median: number
}

export interface OrganicViral {
  multiple: number
  median_views: number | null
  mean_views: number | null
  threshold: number | null
  videos: ViralVideo[]
  note: string | null
}

export interface ViewBucket {
  label: string
  min: number
  max: number | null
  videos: number
  share: number
}

export interface OrganicPerformance {
  days: number
  /** Fecha de la primera foto: desde ahí hay historial. */
  tracking_since: string | null
  followers_series: FollowersPoint[]
  views_series: ViewsPoint[]
  top_videos: OrganicVideo[]
  viral: OrganicViral
  distribution: ViewBucket[]
}

export interface SlotStats {
  videos: number
  avg_views: number | null
}

export interface BestSlot extends SlotStats {
  weekday: number
  weekday_name: string
  hour: number
  low_sample: boolean
}

export interface OrganicPostingTimes {
  days: number
  timezone: string
  videos: number
  weekday_names: string[]
  /** heatmap[día 0 = lunes][hora 0-23] */
  heatmap: SlotStats[][]
  best_slots: BestSlot[]
  by_weekday: (SlotStats & { weekday: number; name: string })[]
  by_hour: (SlotStats & { hour: number })[]
}

export interface TagRow {
  tag: string
  videos: number
  avg_views: number | null
  total_views: number
  engagement: number | null
}

export interface DurationRow {
  label: string
  min_seconds: number
  max_seconds: number | null
  videos: number
  avg_views: number | null
  engagement: number | null
}

export interface OrganicHashtags {
  days: number
  videos: number
  videos_without_hashtags: number
  hashtags: TagRow[]
  mentions: TagRow[]
  durations: DurationRow[]
  best_duration: string | null
}

export interface ShopTotals {
  shop_gmv: number
  shop_orders: number
  shop_units: number
  shop_views: number
  affiliate_orders: number
  affiliate_units: number
  affiliate_sales: number
  affiliate_commission: number
}

export interface ShopVideoRow extends ShopTotals {
  video_id: string
  title: string | null
  share_url: string | null
  organic_views: number
}

export interface OrganicVsShop {
  days: number
  videos_tracked: number
  matched_videos: number
  totals: ShopTotals
  videos: ShopVideoRow[]
  note: string
}

const get = <T>(path: string, params: Record<string, string | undefined> = {}) =>
  authFetch(`${BASE}${path}${query(params)}`).then((r) => parse<T>(r))

export interface VideosParams {
  sort: OrganicSort
  q?: string
  limit: number
  offset: number
}

export const tiktokOrganicData = {
  overview: (days: number) => get<OrganicOverview>('/overview', { days: String(days) }),
  videos: ({ sort, q, limit, offset }: VideosParams) =>
    get<OrganicVideosResponse>('/videos', { sort, q: q?.trim() || undefined, limit: String(limit), offset: String(offset) }),
  performance: (days: number) => get<OrganicPerformance>('/performance', { days: String(days) }),
  postingTimes: (days: number) => get<OrganicPostingTimes>('/posting-times', { days: String(days) }),
  hashtags: (days: number) => get<OrganicHashtags>('/hashtags', { days: String(days) }),
  vsShop: (days: number) => get<OrganicVsShop>('/vs-shop', { days: String(days) }),
}
