-- Atualização do banco: convidados, lista de presentes com cotas e mural de recados.
-- Pode rodar mais de uma vez sem estragar nada (idempotente).
-- Onde rodar: Supabase > projeto do casamento > SQL Editor > colar tudo > Run.

begin;

-- ---------------------------------------------------------------------------
-- 1) Recados: origem (site / casar.com / painel) e mural no site
-- ---------------------------------------------------------------------------
alter table public.recados add column if not exists origem text not null default 'site';
alter table public.recados add column if not exists publicado boolean not null default false;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'recados_origem_check' and conrelid = 'public.recados'::regclass) then
    alter table public.recados add constraint recados_origem_check check (origem in ('site', 'casar', 'manual'));
  end if;
end $$;

-- quem não é admin (o convidado no site) não escolhe origem, data nem publica sozinho
create or replace function public.recados_proteger() returns trigger
language plpgsql set search_path = public as $$
begin
  if not public.is_admin() then
    new.origem := 'site';
    new.publicado := false;
    new.criado_em := now();
  end if;
  return new;
end $$;

drop trigger if exists recados_proteger on public.recados;
create trigger recados_proteger before insert on public.recados
  for each row execute function public.recados_proteger();

drop policy if exists recados_mural_publico on public.recados;
create policy recados_mural_publico on public.recados for select to anon, authenticated using (publicado);

drop policy if exists recados_admin_tudo on public.recados;
create policy recados_admin_tudo on public.recados for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

grant select, insert on public.recados to anon;
grant select, insert, update, delete on public.recados to authenticated;

-- ---------------------------------------------------------------------------
-- 2) Lista de presentes (itens e cotas) - leitura pública dos itens ativos
-- ---------------------------------------------------------------------------
create table if not exists public.presentes (
  id uuid primary key default gen_random_uuid(),
  nome text not null check (char_length(nome) between 2 and 120),
  descricao text check (descricao is null or char_length(descricao) <= 500),
  valor numeric(10, 2) not null check (valor > 0 and valor <= 100000),
  cotas integer not null default 1 check (cotas between 1 and 1000),
  cotas_dadas integer not null default 0,
  imagem text check (imagem is null or char_length(imagem) <= 500),
  ordem integer not null default 0,
  ativo boolean not null default true,
  criado_em timestamptz not null default now()
);
alter table public.presentes enable row level security;

drop policy if exists presentes_publico on public.presentes;
create policy presentes_publico on public.presentes for select to anon, authenticated using (ativo);

drop policy if exists presentes_admin on public.presentes;
create policy presentes_admin on public.presentes for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

revoke all on public.presentes from anon;
grant select on public.presentes to anon;
grant select, insert, update, delete on public.presentes to authenticated;

-- ---------------------------------------------------------------------------
-- 3) Presentes recebidos: o convidado avisa no site depois do Pix;
--    os noivos conferem no painel (e lançam os do casar.com)
-- ---------------------------------------------------------------------------
create table if not exists public.presentes_recebidos (
  id uuid primary key default gen_random_uuid(),
  presente_id uuid references public.presentes (id) on delete set null,
  presente_nome text not null default '' check (char_length(presente_nome) <= 120),
  nome text not null check (char_length(nome) between 2 and 120),
  mensagem text check (mensagem is null or char_length(mensagem) <= 1000),
  cotas integer not null default 1 check (cotas between 1 and 100),
  valor numeric(10, 2) check (valor is null or valor >= 0),
  forma text not null default 'pix' check (forma in ('pix', 'casar', 'outro')),
  origem text not null default 'site' check (origem in ('site', 'casar', 'manual')),
  confirmado boolean not null default false,
  criado_em timestamptz not null default now()
);
create index if not exists presentes_recebidos_presente_idx on public.presentes_recebidos (presente_id);
alter table public.presentes_recebidos enable row level security;

drop policy if exists presentes_recebidos_avisar on public.presentes_recebidos;
create policy presentes_recebidos_avisar on public.presentes_recebidos for insert to anon, authenticated with check (true);

drop policy if exists presentes_recebidos_admin on public.presentes_recebidos;
create policy presentes_recebidos_admin on public.presentes_recebidos for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

revoke all on public.presentes_recebidos from anon;
grant insert on public.presentes_recebidos to anon;
grant select, insert, update, delete on public.presentes_recebidos to authenticated;

-- preenche nome do presente e valor; o convidado não define valor, origem nem "confirmado"
create or replace function public.presentes_recebidos_preparar() returns trigger
language plpgsql set search_path = public as $$
declare
  p record;
begin
  if tg_op = 'INSERT' and not public.is_admin() then
    select id, nome, valor into p from public.presentes where id = new.presente_id and ativo;
    if not found then
      raise exception 'Presente não encontrado' using errcode = '23503';
    end if;
    new.presente_nome := p.nome;
    new.valor := p.valor * new.cotas;
    new.forma := 'pix';
    new.origem := 'site';
    new.confirmado := false;
    new.criado_em := now();
  elsif new.presente_id is not null and (tg_op = 'INSERT' or new.presente_id is distinct from old.presente_id or new.cotas <> old.cotas) then
    select id, nome, valor into p from public.presentes where id = new.presente_id;
    if found then
      if coalesce(new.presente_nome, '') = '' or (tg_op = 'UPDATE' and new.presente_id is distinct from old.presente_id) then
        new.presente_nome := p.nome;
      end if;
      if new.valor is null or (tg_op = 'UPDATE' and new.valor = old.valor) then
        new.valor := p.valor * new.cotas;
      end if;
    end if;
  end if;
  return new;
end $$;

drop trigger if exists presentes_recebidos_preparar on public.presentes_recebidos;
create trigger presentes_recebidos_preparar before insert or update on public.presentes_recebidos
  for each row execute function public.presentes_recebidos_preparar();

-- mantém presentes.cotas_dadas em dia (o site mostra "3 de 10 cotas")
create or replace function public.presentes_recontar() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if tg_op in ('UPDATE', 'DELETE') and old.presente_id is not null then
    update public.presentes set cotas_dadas = coalesce((select sum(cotas) from public.presentes_recebidos where presente_id = old.presente_id), 0)
     where id = old.presente_id;
  end if;
  if tg_op in ('INSERT', 'UPDATE') and new.presente_id is not null then
    update public.presentes set cotas_dadas = coalesce((select sum(cotas) from public.presentes_recebidos where presente_id = new.presente_id), 0)
     where id = new.presente_id;
  end if;
  return null;
end $$;
revoke execute on function public.presentes_recontar() from public, anon, authenticated;

drop trigger if exists presentes_recontar on public.presentes_recebidos;
create trigger presentes_recontar after insert or update or delete on public.presentes_recebidos
  for each row execute function public.presentes_recontar();

-- ---------------------------------------------------------------------------
-- 4) Convidados (lista privada, só os noivos veem)
-- ---------------------------------------------------------------------------
create table if not exists public.convidados (
  id uuid primary key default gen_random_uuid(),
  nome text not null check (char_length(nome) between 2 and 120),
  grupo text check (grupo is null or char_length(grupo) <= 60),
  telefone text check (telefone is null or char_length(telefone) <= 30),
  adultos integer not null default 1 check (adultos between 0 and 20),
  criancas integer not null default 0 check (criancas between 0 and 20),
  resposta text check (resposta in ('sim', 'nao')),
  observacao text check (observacao is null or char_length(observacao) <= 500),
  origem text not null default 'manual' check (origem in ('casar', 'manual', 'planilha')),
  criado_em timestamptz not null default now()
);
alter table public.convidados enable row level security;

drop policy if exists convidados_admin on public.convidados;
create policy convidados_admin on public.convidados for all to authenticated
  using (public.is_admin()) with check (public.is_admin());

revoke all on public.convidados from anon;
grant select, insert, update, delete on public.convidados to authenticated;

commit;

-- avisa a API para enxergar as tabelas novas na hora
notify pgrst, 'reload schema';
