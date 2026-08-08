export interface Firm {
  id: string;
  name: string;
  canonical_name: string;
  asset_classes: string[];
  localities: string[];
  is_active: boolean;
  signal_count?: number;
}

export interface FirmDetail extends Firm {
  description: string | null;
  website: string | null;
  employees_count: number | null;
  founded_year: number | null;
}

export interface Signal {
  id: string;
  firm_id: string;
  signal_type: string;
  title: string;
  body: string | null;
  score: number;
  occurred_at: string | null;
  detected_at: string;
  locality: string | null;
  asset_class: string | null;
}

export interface FirmTimeline {
  firm: FirmDetail;
  signals: Signal[];
  total: number;
  page: number;
  page_size: number;
}

export interface Citation {
  url: string | null;
  page_number: number | null;
  excerpt: string;
}

export interface AskAnswer {
  answer: string;
  citations: Citation[];
  query: string;
  passages_found: number;
}

export interface DigestOut {
  id: string;
  period_start: string;
  period_end: string;
  title: string;
  markdown: string;
  signal_count: number;
  firm_count: number;
  generated_at: string;
}

export interface AlertOut {
  id: string;
  user_id: string;
  name: string;
  channel: string;
  channel_target: string;
  filters: Record<string, unknown>;
  is_active: boolean;
  fire_count: number;
  last_fired_at: string | null;
  created_at: string;
}

export interface AlertCreate {
  user_id: string;
  name: string;
  channel: string;
  channel_target: string;
  filters?: Record<string, unknown>;
}

export interface PaginatedFirms {
  items: Firm[];
  total: number;
  page: number;
  page_size: number;
}
