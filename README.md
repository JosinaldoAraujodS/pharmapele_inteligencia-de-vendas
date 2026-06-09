# Pharmapele — Inteligência de Vendas

Sistema web para importação e análise de NFC-e, inteligência comercial multi-loja/franquia, alertas de recompra e gestão de acesso.

**Stack:** FastAPI · PostgreSQL · SPA (`index.html`) · JWT

**Acesso local:** http://localhost:8000

---

## O que o sistema faz

| Área | Descrição |
|------|-----------|
| **Importação** | Upload de XMLs NFC-e (modelo 65); cria lojas, produtos, clientes e notas automaticamente |
| **Dashboard** | KPIs, filtros de período, faturamento mensal (clique no mês), descontos, categorias, pagamentos, top produtos |
| **Operacional** | Notas fiscais, clientes e produtos com filtros, paginação, detalhes e exportação CSV |
| **Inteligência** | Análises de sazonalidade, comparativo de lojas, ticket, descontos, clientes novos/recorrentes, venda casada |
| **Ação comercial** | Alertas de recompra, clientes inativos e templates WhatsApp |
| **Administração** | CRUD de franquias e usuários; vínculos loja ↔ franquia e operador ↔ loja |

---

## Estrutura do projeto

```
pharmapele_inteligencia-de-vendas/
├── backend/
│   ├── main.py              # API FastAPI (~40 endpoints)
│   ├── requirements.txt
│   └── venv/                # Criado automaticamente pelo iniciar.bat
├── frontend/
│   └── index.html           # Interface SPA (todas as telas)
├── sql/
│   ├── pharmapele_schema_completo.sql   # Schema + seed inicial
│   ├── 01_criar_banco.sql
│   ├── 02_adicionar_lojas.sql
│   ├── 03_suporte_franquias.sql
│   └── 04_many_to_many_franquias.sql
├── iniciar.bat              # Inicia o servidor no Windows
├── updates.txt              # Histórico de evoluções / ideias
└── README.md
```

Na primeira execução, o backend aplica **migrações automáticas** (`02`–`04`) e cria usuários de teste, se ainda não existirem.

---

## Instalação (Windows)

### 1. PostgreSQL

1. Baixe em https://www.postgresql.org/download/windows/ (versão 16+)
2. Anote a senha do usuário `postgres` (porta padrão: **5432**)
3. Instale o **pgAdmin** junto com o PostgreSQL

### 2. Banco de dados

1. Abra o **pgAdmin 4** e conecte ao servidor local
2. Crie o banco `pharmapele` (Databases → Create → Database)
3. Abra **Query Tool** no banco e execute `sql/pharmapele_schema_completo.sql` (**F5**)

As migrações incrementais (`02`–`04`) rodam automaticamente ao iniciar o backend.

### 3. Python

1. Baixe Python **3.11+** em https://www.python.org/downloads/
2. Marque **"Add Python to PATH"** na instalação

### 4. Senha do banco

O `iniciar.bat` usa estas variáveis (com defaults):

| Variável | Padrão no `iniciar.bat` |
|----------|-------------------------|
| `DB_HOST` | `localhost` |
| `DB_PORT` | `5432` |
| `DB_NAME` | `pharmapele` |
| `DB_USER` | `postgres` |
| `DB_PASS` | `Ojuara10*` |

Para usar outra senha, defina antes de iniciar:

```cmd
set DB_PASS=sua_senha_aqui
iniciar.bat
```

Ou edite a linha `set DB_PASS=...` dentro do `iniciar.bat`.

### 5. Iniciar

Dê **duplo clique** em `iniciar.bat`. Na primeira vez, cria o `venv` e instala as dependências.

Abra: **http://localhost:8000**

---

## Usuários de teste

Todos usam a senha **`Ojuara10*`** (criados na migração automática):

| Perfil | E-mail | Escopo |
|--------|--------|--------|
| **Master (matriz)** | `admin@pharmapele.com.br` | Todas as lojas e franquias; administração completa |
| **Franqueado** | `franqueado@pharmapele.com.br` | Lojas da franquia vinculada; pode criar operadores |
| **Operador** | `operador@pharmapele.com.br` | Uma loja apenas |

---

## Perfis de acesso

```
Master ──────► todas as lojas / todas as franquias / admin total
Franqueado ──► lojas das franquias em usuario_franquias / cria operadores
Operador ────► uma loja (usuarios.loja_id)
```

O **seletor de loja** no topo filtra dados em todas as telas. Master e franqueado podem escolher “Todas as lojas”.

**Importante:** lojas importadas via XML podem nascer **sem franquia** (`franquia_id` nulo). A matriz deve vinculá-las em **Administração → Lojas & vínculos**.

Alterações de franquias de um franqueado exigem **novo login** para atualizar o JWT.

---

## Telas e funcionalidades

### Dashboard
- KPIs de faturamento, notas, clientes e ticket médio
- Filtro por período (histórico, mês atual, 30 dias); clique nos cartões leva às telas relacionadas
- Gráfico de faturamento mensal (clique no mês filtra o restante)
- Descontos concedidos (total, % sobre bruto, evolução mensal)
- Receita por categoria e formas de pagamento
- Top 10 produtos e preview de alertas de recompra

### Importar XMLs
- Arraste um ou vários `.xml` de NFC-e
- Valida CNPJ da loja conforme permissão do usuário logado
- Cadastra/atualiza loja, produtos, cliente e itens da nota

### Notas Fiscais
- Busca por NF, cliente; filtro de período e forma de pagamento
- Paginação; colunas bruto, desconto e total
- Clique na linha abre detalhe (itens, pagamento, chave NFC-e)
- Atalho para histórico do cliente
- Exportar CSV

### Clientes
- Busca, paginação, total gasto e última compra
- Modal com histórico de compras e produtos favoritos
- CPF não exibido na listagem (dado sensível)

### Produtos
- Filtros: período, categoria, busca por nome
- Ordenação por unidades, receita ou clientes
- Detalhe: clientes que compraram, últimas vendas, preço/desconto médio
- Modo **produtos parados** (sem venda no período)
- Exportar CSV

### Análises
Hub de inteligência com filtros de período e navegação por âncoras:

- **Sazonalidade** — top 3 e gráficos por dia da semana, dia do mês e hora
- **Top lojas** — comparativo (só com 2+ lojas no escopo)
- **Top 5 produtos por loja**
- **Novos vs recorrentes** — saúde da base de clientes identificados
- **Ticket médio** — por pagamento, categoria e loja
- **Descontos** — por categoria e produto
- **Vendas por cidade** — município/UF dos clientes identificados

### Venda Casada
- Pares de produtos comprados na mesma nota (market basket)
- Cada combo aparece uma vez (A+B = B+A), agrupado por nome do produto
- Confiança nos dois sentidos: “comprou A → sugira B” e vice-versa
- Filtros: período, busca, mínimo de notas e confiança

### Alertas de Recompra
- Calculado por **cliente + produto** (mínimo 2 compras no histórico)
- Intervalo médio entre compras define a data estimada de recompra
- Janela: 7, 14 ou 30 dias; filtros de busca, telefone e atrasados
- Botão WhatsApp com mensagem personalizada

### Clientes Inativos
- Clientes sem compra há 60+ dias
- Produto favorito e último produto para personalizar abordagem
- Paginação, filtros e link WhatsApp

### Mensagens WhatsApp
- Templates: recompra, inativo, fidelidade
- Lista de contatos prioritários do dia

### Administração
Visível para **master** e **franqueado**:

| Recurso | Master | Franqueado |
|---------|--------|------------|
| Franquias (criar, editar, ativar) | Sim | Não |
| Usuários (todos os perfis) | Sim | Só operadores das suas lojas |
| Vincular loja ↔ franquia | Sim | Não |
| Vincular franqueado ↔ franquias | Sim | Não |
| Vincular operador ↔ loja | Sim | Sim (lojas do escopo) |
| Redefinir senha | Sim | Sim (operadores do escopo) |

---

## API (resumo)

Principais grupos de rotas em `backend/main.py`:

| Grupo | Exemplos |
|-------|----------|
| Auth | `POST /api/login` |
| Dados | `/api/dashboard`, `/api/notas`, `/api/produtos`, `/api/clientes` |
| Comercial | `/api/alertas-recompra`, `/api/clientes-inativos` |
| Análises | `/api/analises`, `/api/analises/cestas` |
| Admin | `/api/admin/franquias`, `/api/admin/usuarios`, `/api/admin/lojas` |
| Utilitários | Export CSV, upload XML, listagem de lojas |

Documentação interativa (com servidor rodando): http://localhost:8000/docs

---

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `DB_HOST` | `localhost` | Host PostgreSQL |
| `DB_PORT` | `5432` | Porta PostgreSQL |
| `DB_NAME` | `pharmapele` | Nome do banco |
| `DB_USER` | `postgres` | Usuário do banco |
| `DB_PASS` | `Ojuara10*` (no bat) | Senha do banco |
| `JWT_SECRET` | *(valor fixo no código)* | Chave do token JWT — **altere em produção** |
| `SKIP_MIGRATIONS` | — | `true` para pular migrações (ex.: serverless) |

---

## Roadmap

### Concluído
- [x] Multi-loja e multi-franquia com JWT e filtros de segurança
- [x] Dashboard com filtros, descontos e drill-down
- [x] Notas, clientes e produtos (filtros, paginação, detalhe, CSV)
- [x] Análises: sazonalidade, top lojas/produtos, ticket, descontos, novos vs recorrentes, cidades
- [x] Venda casada (cross-sell / market basket)
- [x] Administração: franquias, usuários e vínculos
- [x] Navegação por âncoras na tela Análises

### Pendente
- [ ] Integração Evolution API (disparo automático WhatsApp)
- [ ] Importação em lote de pasta inteira de XMLs
- [ ] Relatórios exportáveis em PDF
- [ ] Painel de metas mensais
- [ ] Seletor de franquia no topo (além de loja)
- [ ] Vincular `franquia_id` automaticamente na importação de XML

---

## Problemas comuns

**Não conecta ao servidor**
→ Verifique se o PostgreSQL está ativo e se o `iniciar.bat` está rodando.

**Erro ao importar XML**
→ Confirme NFC-e modelo 65 válida. Operador/franqueado só importa CNPJ da(s) loja(s) permitida(s).

**Nenhum alerta de recompra**
→ É necessário ao menos **2 compras** do mesmo produto pelo mesmo cliente identificado.

**Franqueado não vê lojas**
→ A loja pode estar sem `franquia_id`. Master deve vincular em Administração.

**Administração não aparece no menu**
→ Só master e franqueado veem o item. Operador não tem acesso.

**Alterou franquias/usuário e permissão não mudou**
→ Faça logout e login novamente (JWT carrega vínculos no momento do login).

---

## Licença e uso

Projeto interno **Grupo Pharmapele** · v1.1
