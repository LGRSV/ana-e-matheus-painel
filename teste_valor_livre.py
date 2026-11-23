"""Teste local do valor livre no painel (Playwright, cliente Supabase falso, sem rede real)."""
import os, sys
from playwright.sync_api import sync_playwright

AQUI = os.path.dirname(os.path.abspath(__file__))
CAP = os.path.abspath(os.path.join(AQUI, "..", "casamento-ana-matheus", "capturas"))
os.makedirs(CAP, exist_ok=True)

FAKE = r"""
window.__calls = [];
(function(){
  var cotas = [
    {id: 1, nome: "Jantar na lua de mel", valor: 150, ordem: 0, limite: null, pagos: 0, descricao: "", imagem: null, valor_livre: false, valor_minimo: null},
    {id: 2, nome: "Liquidificador", valor: 200, ordem: 1, limite: 1, pagos: 0, descricao: "", imagem: null, valor_livre: true, valor_minimo: 30},
    {id: 3, nome: "Panela", valor: 250, ordem: 2, limite: 1, pagos: 0, descricao: "", imagem: null, valor_livre: false, valor_minimo: null}
  ];
  var proximo = 10;
  function chain(tabela, op, payload) {
    var f = {}, q = { filtros: [] };
    function fim() {
      if (op !== "select") window.__calls.push({ tipo: "db", tabela: tabela, op: op, payload: payload, filtros: q.filtros });
      var data = [];
      if (tabela === "admins") data = { login: "teste" };
      else if (tabela === "presentes" && op === "select") data = cotas;
      else if (tabela === "presentes" && op === "update") {
        var ids = q.filtros.filter(function (x) { return x[0] === "in"; }).map(function (x) { return x[1]; })[0]
          || q.filtros.filter(function (x) { return x[0] === "eq"; }).map(function (x) { return [x[1]]; })[0] || [];
        data = cotas.filter(function (c) { return ids.indexOf(c.id) >= 0; });
        data.forEach(function (c) { Object.assign(c, payload); });
      } else if (tabela === "presentes" && op === "insert") {
        var nova = Object.assign({ id: proximo++, pagos: 0 }, payload); cotas.push(nova); data = nova;
      }
      return Promise.resolve({ data: data, error: null });
    }
    ["select","order","eq","limit","is","neq","in","gte","lte"].forEach(function (m) {
      f[m] = function () { if (m === "eq") q.filtros.push(["eq", arguments[1]]); if (m === "in") q.filtros.push(["in", arguments[1]]); return f; };
    });
    f.single = fim; f.maybeSingle = fim; f.then = function (a, b) { return fim().then(a, b); };
    return f;
  }
  var user = { id: "u1", email: "teste@x" };
  window.supabase = { createClient: function () { return {
    auth: {
      getSession: function () { return Promise.resolve({ data: { session: { user: user, access_token: "t" } } }); },
      onAuthStateChange: function () { return { data: { subscription: { unsubscribe: function () {} } } }; },
      signOut: function () { return Promise.resolve({}); }, signInWithPassword: function () { return Promise.resolve({ error: { message: "fake" } }); }
    },
    from: function (t) { return {
      select: function () { return chain(t, "select"); },
      update: function (p) { return chain(t, "update", p); },
      insert: function (p) { return chain(t, "insert", p); },
      delete: function () { return chain(t, "delete"); }
    }; },
    storage: { from: function () { return { upload: function () { return Promise.resolve({ error: null }); }, getPublicUrl: function (n) { return { data: { publicUrl: "x/" + n } }; }, remove: function () { return Promise.resolve({ error: null }); } }; } },
    rpc: function () { return Promise.resolve({ data: null, error: null }); }
  }; } };
})();
"""

erros, falhas = [], []
def ok(c, m):
    print(("OK   " if c else "FALHA ") + m)
    if not c: falhas.append(m)

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 760, "height": 1100})
    pg.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
    pg.on("pageerror", lambda e: erros.append(str(e)))
    pg.route("**/supabase-js@**", lambda r: r.fulfill(status=200, content_type="application/javascript", body=FAKE))
    pg.route("**/fonts.g*", lambda r: r.abort())
    pg.route("**/qrcode-generator@**", lambda r: r.fulfill(status=200, content_type="application/javascript", body="window.qrcode=function(){return {}}"))
    pg.route("https://gypllpiiggmklpkqapyz.supabase.co/functions/**", lambda r: r.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body="{}"))
    dialogos = []
    pg.on("dialog", lambda d: (dialogos.append(d.message), d.accept()))
    pg.goto("file:///" + os.path.join(AQUI, "index.html").replace("\\", "/"))
    pg.wait_for_selector("[data-aba=presentes]", state="visible", timeout=8000)
    pg.click("[data-aba=presentes]")
    pg.wait_for_selector("#lista-cotas .item")

    itens = pg.locator("#lista-cotas .item")
    ok(itens.nth(1).locator(".selo.livre").count() == 1 and "valor livre" in itens.nth(1).inner_text(), "lista: selo 'valor livre' no item livre")
    ok(itens.nth(0).locator(".selo.livre").count() == 0, "lista: item fixo sem selo")
    ok("a partir de R$ 30,00" in itens.nth(1).inner_text().replace(" ", " "), "lista: mostra 'a partir de' com o minimo")
    pg.screenshot(path=os.path.join(CAP, "valor-livre-painel-lista.png"))

    # editar item fixo: ligar valor livre com minimo
    itens.nth(0).get_by_text("Editar", exact=True).click()
    ok(not pg.is_checked("#f-cota [name=valor_livre]"), "form: chave desligada no item fixo")
    ok(pg.is_hidden("#rot-valor-minimo"), "form: campo minimo escondido com a chave desligada")
    pg.check("#f-cota [name=valor_livre]")
    ok(pg.is_visible("#rot-valor-minimo"), "form: ligar a chave mostra o minimo")
    pg.fill("#f-cota [name=valor_minimo]", "0,50")
    pg.click("#f-cota button[type=submit]")
    ok("mÃ­nimo" in pg.inner_text("#f-cota .aviso"), "form: minimo invalido recusado")
    pg.fill("#f-cota [name=valor_minimo]", "25,50")
    pg.screenshot(path=os.path.join(CAP, "valor-livre-painel-form.png"))
    pg.click("#f-cota button[type=submit]")
    pg.wait_for_function("window.__calls.some(c => c.op==='update')")
    upd = [c for c in pg.evaluate("window.__calls") if c["op"] == "update"][0]["payload"]
    ok(upd["valor_livre"] is True and upd["valor_minimo"] == 25.5, "salvar grava valor_livre=true, valor_minimo=25.5 (%s, %s)" % (upd["valor_livre"], upd["valor_minimo"]))
    pg.wait_for_function("document.querySelectorAll('#lista-cotas .selo.livre').length === 2")
    ok(True, "lista atualiza com 2 itens livres")

    # editar item livre e desligar: minimo some do payload
    pg.evaluate("window.__calls.length = 0")
    pg.locator("#lista-cotas .item").nth(1).get_by_text("Editar", exact=True).click()
    ok(pg.is_checked("#f-cota [name=valor_livre]") and pg.input_value("#f-cota [name=valor_minimo]") == "30,00", "form: carrega chave e minimo do item")
    pg.uncheck("#f-cota [name=valor_livre]")
    pg.click("#f-cota button[type=submit]")
    pg.wait_for_function("window.__calls.some(c => c.op==='update')")
    upd = [c for c in pg.evaluate("window.__calls") if c["op"] == "update"][0]["payload"]
    ok(upd["valor_livre"] is False and upd["valor_minimo"] is None, "desligar grava valor_livre=false e minimo nulo")

    # nova cota com valor livre
    pg.evaluate("window.__calls.length = 0")
    pg.click("#abrir-cota")
    pg.fill("#f-cota [name=nome]", "Lua de mel livre"); pg.fill("#f-cota [name=valor]", "100")
    pg.check("#f-cota [name=valor_livre]")
    pg.click("#f-cota button[type=submit]")
    pg.wait_for_function("window.__calls.some(c => c.op==='insert')")
    ins = [c for c in pg.evaluate("window.__calls") if c["op"] == "insert"][0]["payload"]
    ok(ins["valor_livre"] is True and ins["valor_minimo"] is None, "nova cota livre sem minimo: valor_minimo nulo")

    # atalhos em massa
    pg.evaluate("window.__calls.length = 0"); dialogos.clear()
    pg.click("#livre-ligar-todos")
    pg.wait_for_function("window.__calls.some(c => c.op==='update')")
    ok(len(dialogos) == 1 and "TODAS" in dialogos[0], "ligar em todos pede confirmacao (%s)" % (dialogos[0][:40] if dialogos else None))
    u = [c for c in pg.evaluate("window.__calls") if c["op"] == "update"][0]
    ok(u["payload"]["valor_livre"] is True and len(u["filtros"][0][1]) == 4, "ligar em todos: update com os 4 ids")
    pg.wait_for_function("document.querySelectorAll('#lista-cotas .selo.livre').length === 4")
    ok("ligado em 4" in pg.inner_text("#livre-estado"), "mensagem de resultado: %s" % pg.inner_text("#livre-estado"))
    pg.screenshot(path=os.path.join(CAP, "valor-livre-painel-todos.png"))
    pg.evaluate("window.__calls.length = 0")
    pg.click("#livre-desligar-todos")
    pg.wait_for_function("document.querySelectorAll('#lista-cotas .selo.livre').length === 0")
    ok(len(dialogos) == 2, "desligar em todos tambem confirma e zera os selos")

    b.close()

ruido = [e for e in erros if "fonts" not in e.lower() and "ERR_FAILED" not in e]
ok(not ruido, "console sem erros %s" % ruido)
print("RESULTADO:", "tudo certo" if not falhas else "%d falha(s)" % len(falhas))
sys.exit(1 if falhas else 0)
