import { CHANNEL_COLOR, CHANNEL_LABEL, type Channel } from '@/lib/api'

export function ChannelBadge({ channel }: { channel: Channel }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className="size-2.5 rounded-full" style={{ backgroundColor: CHANNEL_COLOR[channel] }} />
      {CHANNEL_LABEL[channel]}
    </span>
  )
}
