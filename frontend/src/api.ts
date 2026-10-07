export interface MediaInfo {
  duration: number;
  width: number;
  height: number;
  fps: number;
  video_codec: string;
  audio_codec: string | null;
  has_audio: boolean;
  size_bytes: number;
}

export interface UploadResult {
  upload_id: string;
  metadata: MediaInfo;
  thumbnail_urls: string[];
}

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Candidate {
  start: number;
  end: number;
  title: string;
  reason: string;
  score: number;
}

export interface Job {
  id: string;
  status: JobStatus;
  percent: number;
  error: string | null;
  download_url: string | null;
  requested_start: number | null;
  effective_start: number | null;
  warning: string | null;
  phase: string | null;
  subtitle_status: string | null;
  subtitle_url: string | null;
  detected_language: string | null;
  kind: "clip" | "analyze";
  aspect: "original" | "vertical";
  title: string | null;
  candidates: Candidate[] | null;
}

async function asError(response: Response): Promise<never> {
  let detail = response.statusText;
  try {
    const body = await response.json();
    if (body?.detail) detail = body.detail;
  } catch {
    /* ignore */
  }
  throw new Error(detail);
}

export function uploadVideo(
  file: File,
  onProgress: (percent: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/uploads");
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        onProgress((event.loaded / event.total) * 100);
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText) as UploadResult);
      } else {
        let detail = xhr.statusText;
        try {
          detail = JSON.parse(xhr.responseText).detail ?? detail;
        } catch {
          /* ignore */
        }
        reject(new Error(detail));
      }
    };
    xhr.onerror = () => reject(new Error("upload failed"));
    xhr.send(form);
  });
}

export async function createClip(payload: {
  upload_id: string;
  start: number;
  end: number;
  mode: "copy" | "reencode";
  scale_height?: number | null;
  subtitles?: "none" | "srt";
  translate_to?: string | null;
  aspect?: "original" | "vertical";
  title?: string | null;
}): Promise<Job> {
  const response = await fetch("/api/clips", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) return asError(response);
  return (await response.json()) as Job;
}

export async function getJob(id: string): Promise<Job> {
  const response = await fetch(`/api/jobs/${id}`);
  if (!response.ok) return asError(response);
  return (await response.json()) as Job;
}

export async function cancelJob(id: string): Promise<void> {
  await fetch(`/api/jobs/${id}`, { method: "DELETE" });
}

export async function analyzeVideo(payload: {
  upload_id: string;
  min_length: number;
  max_length: number;
  max_clips: number;
}): Promise<Job> {
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) return asError(response);
  return (await response.json()) as Job;
}

export async function createClipsBatch(payload: {
  upload_id: string;
  clips: { start: number; end: number; title?: string | null }[];
  mode: "copy" | "reencode";
  subtitles?: "none" | "srt";
  translate_to?: string | null;
  aspect?: "original" | "vertical";
}): Promise<Job[]> {
  const response = await fetch("/api/clips/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) return asError(response);
  return (await response.json()) as Job[];
}

export interface Resource {
  id: string;
  upload_id: string;
  name: string;
  start: number;
  end: number;
  duration: number;
  mode: string;
  created: number;
  size: number;
  download_url: string;
  thumbnail_url: string | null;
  subtitle_url: string | null;
  detected_language: string | null;
  translated_to: string | null;
}

export async function listResources(): Promise<Resource[]> {
  const response = await fetch("/api/resources");
  if (!response.ok) return asError(response);
  const body = (await response.json()) as { items: Resource[] };
  return body.items;
}

export async function deleteResource(id: string): Promise<void> {
  await fetch(`/api/resources/${id}`, { method: "DELETE" });
}

export function subscribeJob(
  id: string,
  onUpdate: (job: Job) => void,
): EventSource {
  const source = new EventSource(`/api/jobs/${id}/events`);
  source.onmessage = (event) => {
    onUpdate(JSON.parse(event.data) as Job);
  };
  return source;
}
