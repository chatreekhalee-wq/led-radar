-- =====================================================================
-- LED Radar — ฐานข้อมูลระบบคัดทรัพย์ขายทอดตลาดกรมบังคับคดี
-- รันไฟล์นี้ทั้งไฟล์ใน Supabase > SQL Editor (รันซ้ำได้ ไม่ทำข้อมูลหาย)
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- 1) ค่าตั้งระบบที่เป็นความลับ (token ของปุ่มเก็บข้อมูล) — ไม่มีใครอ่านได้นอกจาก service role
-- ---------------------------------------------------------------------
create table if not exists app_secrets (
  key   text primary key,
  value text not null
);
alter table app_secrets enable row level security;  -- ไม่มี policy = anon/auth อ่านไม่ได้

-- อีเมลที่มีสิทธิ์แก้ watchlist / โน้ต / พิกัด บน Dashboard
create table if not exists app_admins (
  email text primary key
);
alter table app_admins enable row level security;

create or replace function is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from app_admins where email = (auth.jwt() ->> 'email'));
$$;

-- ---------------------------------------------------------------------
-- 2) ข้อมูลดิบจากปุ่มเก็บข้อมูล (เก็บทั้งหน้า เพื่อ parse ใหม่ได้เมื่อปรับ parser)
-- ---------------------------------------------------------------------
create table if not exists raw_captures (
  id           bigserial primary key,
  captured_at  timestamptz not null default now(),
  page_url     text not null,
  page_title   text,
  page_kind    text,                 -- 'list' | 'detail' | 'unknown' (ฝั่งเบราว์เซอร์เดาให้)
  tables_html  text,                 -- เฉพาะ <table> ในหน้า
  page_text    text,                 -- innerText ของหน้า
  links        jsonb,                -- [{text, href}]
  images       jsonb,                -- [src]
  status       text not null default 'new',   -- new | parsed | error
  parse_error  text,
  parsed_at    timestamptz
);
alter table raw_captures enable row level security;
create index if not exists raw_captures_status_idx on raw_captures(status);

-- RPC ที่ปุ่มเก็บข้อมูลเรียก: ตรวจ token แล้ว insert (anon insert ตรงไม่ได้)
create or replace function submit_capture(
  p_token text, p_url text, p_title text, p_kind text,
  p_tables_html text, p_page_text text, p_links jsonb, p_images jsonb
) returns bigint
language plpgsql security definer set search_path = public as $$
declare v_id bigint;
begin
  if p_token is null or p_token <> (select value from app_secrets where key = 'capture_token') then
    raise exception 'invalid capture token';
  end if;
  if p_url !~* '^https?://([a-z0-9-]+\.)*led\.go\.th/' then
    raise exception 'only led.go.th pages are accepted';
  end if;
  if coalesce(length(p_tables_html),0) + coalesce(length(p_page_text),0) > 3000000 then
    raise exception 'capture too large';
  end if;
  insert into raw_captures(page_url, page_title, page_kind, tables_html, page_text, links, images)
  values (p_url, left(p_title, 500), p_kind, p_tables_html, p_page_text,
          coalesce(p_links, '[]'::jsonb), coalesce(p_images, '[]'::jsonb))
  returning id into v_id;
  return v_id;
end $$;
revoke all on function submit_capture(text,text,text,text,text,text,jsonb,jsonb) from public;
grant execute on function submit_capture(text,text,text,text,text,text,jsonb,jsonb) to anon, authenticated;

-- ---------------------------------------------------------------------
-- 3) ทรัพย์ (1 แถว = 1 รายการประกาศ)
-- ---------------------------------------------------------------------
create table if not exists assets (
  id              text primary key,          -- key ที่ worker สร้าง: เลขคดี+ลำดับ หรือ hash
  source_url      text,
  case_no         text,                      -- เลขคดีแดง
  lot_no          text,                      -- ลำดับที่/ชุดที่
  office          text,                      -- สำนักงานบังคับคดี
  asset_type      text,                      -- ที่ดินเปล่า | บ้านเดี่ยว | ทาวน์เฮ้าส์ | ห้องชุด | อาคารพาณิชย์ | อื่นๆ
  deed_type       text,                      -- โฉนด | น.ส.3ก | ห้องชุด ...
  deed_no         text,
  province        text,
  amphoe          text,
  tambon          text,
  address_text    text,
  area_rai        numeric default 0,
  area_ngan       numeric default 0,
  area_wah        numeric default 0,
  area_sqm        numeric,                   -- ห้องชุด/อาคาร
  appraisal_price numeric,                   -- ราคาประเมินเจ้าพนักงาน (ฐานคิด % ต่อนัด)
  appraisal_dept  numeric,                   -- ราคาประเมินกรมธนารักษ์ (ถ้ามี)
  deposit         numeric,                   -- หลักประกัน
  sale_condition  text,                      -- เงื่อนไขการขาย / หมายเหตุ (ใช้หาสัญญาณเสี่ยง)
  occupant_note   text,
  status          text default 'active',     -- active | sold | withdrawn | ended
  lat             double precision,
  lng             double precision,
  geo_precision   text,                      -- 'manual' | 'address' | 'tambon' | 'amphoe' | null
  images          jsonb default '[]'::jsonb,
  raw             jsonb,                     -- ฟิลด์ทั้งหมดที่ parse ได้
  first_seen      timestamptz default now(),
  last_seen       timestamptz default now(),
  updated_at      timestamptz default now()
);
create index if not exists assets_prov_idx on assets(province, amphoe);
create index if not exists assets_type_idx on assets(asset_type);

-- นัดขาย 1–6 ของแต่ละทรัพย์ (+ผลแต่ละนัด) — ประวัตินี้คือหัวใจของการทำนายราคา
create table if not exists auction_rounds (
  asset_id    text references assets(id) on delete cascade,
  round_no    int  not null,
  sale_date   date,
  result      text,         -- null(ยังไม่ถึง) | 'งดขาย' | 'ไม่มีผู้ซื้อ' | 'ขายได้' | 'ถอนการยึด' | ...
  start_price numeric,      -- ราคาเริ่มต้นนัดนั้น (คำนวณจากกติกา ถ้าประกาศไม่ระบุ)
  sold_price  numeric,
  primary key (asset_id, round_no)
);

-- ประวัติการเปลี่ยนแปลงราคา/สถานะ (จับการลดราคา)
create table if not exists price_history (
  id          bigserial primary key,
  asset_id    text references assets(id) on delete cascade,
  seen_at     timestamptz default now(),
  round_no    int,
  start_price numeric,
  status      text
);

-- ---------------------------------------------------------------------
-- 4) ข้อมูลทำเล
-- ---------------------------------------------------------------------
create table if not exists geo_cache (           -- แคชผล geocode (เคารพ rate limit Nominatim)
  query      text primary key,
  lat        double precision,
  lng        double precision,
  precision  text,
  fetched_at timestamptz default now()
);

create table if not exists asset_location (     -- ระยะ/จำนวน POI ต่อทรัพย์
  asset_id         text primary key references assets(id) on delete cascade,
  computed_at      timestamptz default now(),
  lat              double precision,
  lng              double precision,
  d_main_road_m    numeric,     -- ถนน trunk/primary/secondary ที่ใกล้สุด
  d_hospital_m     numeric,
  d_school_m       numeric,
  d_market_m       numeric,     -- ตลาด/ซูเปอร์/ห้าง
  d_rail_m         numeric,     -- สถานีรถไฟ/รถไฟฟ้า
  d_industrial_m   numeric,     -- นิคม/เขตอุตสาหกรรม
  n_amenity_2km    int,         -- จำนวนสิ่งอำนวยความสะดวกในรัศมี 2 กม.
  n_building_1km   int,         -- ความหนาแน่นชุมชน (proxy)
  raw              jsonb
);

-- ---------------------------------------------------------------------
-- 5) คะแนน (worker คิดคะแนนย่อย 0–100; Dashboard ถ่วงน้ำหนักตาม preset เอง)
-- ---------------------------------------------------------------------
create table if not exists asset_scores (
  asset_id          text primary key references assets(id) on delete cascade,
  computed_at       timestamptz default now(),
  next_round_no     int,
  next_sale_date    date,
  next_start_price  numeric,
  price_ratio       numeric,     -- next_start_price / ราคาอ้างอิง
  unit_price        numeric,     -- บาท/ตร.วา หรือ บาท/ตร.ม.
  unit_kind         text,        -- 'wah' | 'sqm'
  local_median_unit numeric,     -- ค่ากลางราคาต่อหน่วยของอำเภอ+ประเภทเดียวกัน (จากข้อมูลที่เก็บ)
  local_comps       int,
  s_discount        numeric,
  s_unit_price      numeric,
  s_location        numeric,
  s_location_rent   numeric,
  s_location_land   numeric,
  s_competition     numeric,
  risk_penalty      numeric,     -- 0–100 (ยิ่งมากยิ่งเสี่ยง)
  risk_flags        jsonb default '[]'::jsonb,
  score_rent        numeric,
  score_flip        numeric,
  score_land        numeric
);

-- ---------------------------------------------------------------------
-- 6) ข้อมูลของผู้ใช้ (watchlist / โน้ตลงพื้นที่ / พิกัดแม่นจาก LandsMaps)
-- ---------------------------------------------------------------------
create table if not exists asset_user (
  asset_id     text primary key references assets(id) on delete cascade,
  watch        boolean default false,
  site_visit   text,            -- 'ยังไม่ไป' | 'ไปแล้ว-ผ่าน' | 'ไปแล้ว-ไม่ผ่าน'
  note         text,
  manual_lat   double precision,
  manual_lng   double precision,
  max_bid      numeric,         -- เพดานราคาที่ตั้งใจสู้
  updated_at   timestamptz default now()
);

-- แจ้งเตือนที่ส่งแล้ว (กันส่งซ้ำ)
create table if not exists alerts_sent (
  asset_id  text,
  kind      text,               -- 'new_high' | 'round_soon' | 'price_drop'
  ref       text,               -- เช่น round_no / วันที่
  sent_at   timestamptz default now(),
  primary key (asset_id, kind, ref)
);

-- ---------------------------------------------------------------------
-- 7) View สำหรับ Dashboard (อ่านอย่างเดียวผ่าน anon key)
-- ---------------------------------------------------------------------
create or replace view v_dashboard as
select
  a.id, a.source_url, a.case_no, a.lot_no, a.office, a.asset_type, a.deed_type, a.deed_no,
  a.province, a.amphoe, a.tambon, a.address_text,
  a.area_rai, a.area_ngan, a.area_wah, a.area_sqm,
  a.appraisal_price, a.appraisal_dept, a.deposit, a.sale_condition, a.occupant_note, a.status,
  coalesce(u.manual_lat, a.lat) as lat,
  coalesce(u.manual_lng, a.lng) as lng,
  case when u.manual_lat is not null then 'manual' else a.geo_precision end as geo_precision,
  a.images, a.first_seen, a.last_seen,
  s.next_round_no, s.next_sale_date, s.next_start_price, s.price_ratio,
  s.unit_price, s.unit_kind, s.local_median_unit, s.local_comps,
  s.s_discount, s.s_unit_price, s.s_location, s.s_location_rent, s.s_location_land,
  s.s_competition, s.risk_penalty, s.risk_flags, s.score_rent, s.score_flip, s.score_land,
  l.d_main_road_m, l.d_hospital_m, l.d_school_m, l.d_market_m, l.d_rail_m, l.d_industrial_m,
  l.n_amenity_2km, l.n_building_1km,
  coalesce(u.watch,false) as watch, u.site_visit, u.note, u.max_bid,
  (select jsonb_agg(jsonb_build_object('round',r.round_no,'date',r.sale_date,'result',r.result,
          'start_price',r.start_price,'sold_price',r.sold_price) order by r.round_no)
     from auction_rounds r where r.asset_id = a.id) as rounds
from assets a
left join asset_scores   s on s.asset_id = a.id
left join asset_location l on l.asset_id = a.id
left join asset_user     u on u.asset_id = a.id;

-- ---------------------------------------------------------------------
-- 8) สิทธิ์ (RLS)
-- ---------------------------------------------------------------------
alter table assets          enable row level security;
alter table auction_rounds  enable row level security;
alter table price_history   enable row level security;
alter table geo_cache       enable row level security;
alter table asset_location  enable row level security;
alter table asset_scores    enable row level security;
alter table asset_user      enable row level security;
alter table alerts_sent     enable row level security;

-- อ่านได้ทุกคน (ข้อมูลประกาศสาธารณะ) — ถ้าอยากให้เห็นเฉพาะตัวเอง เปลี่ยน `true` เป็น is_admin()
do $$ declare t text; begin
  foreach t in array array['assets','auction_rounds','asset_location','asset_scores','price_history'] loop
    execute format('drop policy if exists read_all on %I', t);
    execute format('create policy read_all on %I for select using (true)', t);
  end loop;
end $$;

-- asset_user: อ่าน/เขียนเฉพาะ admin (โน้ตส่วนตัวไม่หลุด)
drop policy if exists admin_rw on asset_user;
create policy admin_rw on asset_user for all using (is_admin()) with check (is_admin());

-- สิทธิ์ระดับตาราง (ระบุชัดเจน ไม่พึ่งค่าเริ่มต้นของโปรเจกต์)
grant usage on schema public to anon, authenticated;
grant select on assets, auction_rounds, asset_location, asset_scores, price_history to anon, authenticated;
grant select on asset_user to anon;            -- RLS ให้เห็น 0 แถว (ต้องมีเพื่อให้ view join ได้)
grant select, insert, update, delete on asset_user to authenticated;
revoke all on app_secrets, app_admins, raw_captures, geo_cache, alerts_sent from anon, authenticated;

-- view ใช้สิทธิ์ของผู้เรียก (Postgres 15+) → anon จะเห็นคอลัมน์ asset_user เป็น null
alter view v_dashboard set (security_invoker = true);
grant select on v_dashboard to anon, authenticated;

-- ---------------------------------------------------------------------
-- 9) ตั้งค่าครั้งแรก — แก้ 2 บรรทัดนี้แล้วรัน
-- ---------------------------------------------------------------------
-- insert into app_secrets(key, value) values ('capture_token', 'เปลี่ยนเป็นรหัสยาวๆ-สุ่มเอง')
--   on conflict (key) do update set value = excluded.value;
-- insert into app_admins(email) values ('อีเมลของคุณ@gmail.com') on conflict do nothing;
