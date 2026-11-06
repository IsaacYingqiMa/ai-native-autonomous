/** Exact control intents; ordinary business text remains feedback for the coordinator. */
export type EntryIntent = "continue" | "status" | "diagnose" | "pause" | "resume" | "abandon" | "review" | "feedback";

export function entryIntent(text: string): EntryIntent {
  const value = text.trim().toLowerCase().replace(/[。！？!?.，,]+$/u, "").replace(/^(?:请|麻烦)(?:帮我)?/u, "").trim();
  if (!value || /^(?:继续|继续执行|继续开发|继续(?:之前的|上次的|当前|这个)任务|接着做|往下做|continue)$/u.test(value)) return "continue";
  if (/^(?:状态|查看状态|查看进度|当前进度|到哪了|现在到哪了|status)$/u.test(value)) return "status";
  if (/^(?:诊断|检查环境|检查安装|检查一下环境|doctor)$/u.test(value)) return "diagnose";
  if (/^(?:暂停|先暂停|暂停一下|暂停当前任务|暂停这个任务|pause)$/u.test(value)) return "pause";
  if (/^(?:恢复|恢复执行|恢复任务|resume)$/u.test(value)) return "resume";
  if (/^(?:放弃|放弃任务|放弃当前任务|放弃这个任务|放弃这个变更|不要了|不做了|取消当前任务|取消这个变更|abandon)$/u.test(value)) return "abandon";
  if (/^(?:确认|确认需求|批准需求|开始开发|确认开始|验收|验收通过|接受交付|通过|可以了|好|好的|同意|approve|accept)$/u.test(value)) return "review";
  return "feedback";
}
