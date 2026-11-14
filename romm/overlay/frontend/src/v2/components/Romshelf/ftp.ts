// Send with FTP: whether it is on, fetched once per page load and shared by every button.
import { ref } from "vue";
import romshelfApi, { type FtpStatus } from "@/services/api/romshelf";

const OFF: FtpStatus = {
  enabled: false,
  name: null,
  default: null,
  targets: [],
};
const status = ref<FtpStatus>(OFF);
let loading: Promise<void> | null = null;

export function useFtpStatus(refresh = false) {
  if (!loading || refresh) {
    loading = romshelfApi
      .getFtpStatus()
      .then(({ data }) => {
        status.value = data;
      })
      .catch(() => {
        status.value = OFF;
      });
  }
  return status;
}

/** The target to use: `wanted` if it is still set up, else the default. */
export function pickTarget(s: FtpStatus, wanted: string | null | undefined) {
  return s.targets.find((t) => t.id === wanted) ?? s.targets[0] ?? null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}
