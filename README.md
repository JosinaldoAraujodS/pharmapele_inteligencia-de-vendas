# 🧴 Pharmapele — Inteligência de Vendas

Sistema web para análise de notas fiscais (NFC-e), alertas de recompra e estratégias de vendas.

---

## 📁 Estrutura do Projeto

```
pharmapele/
├── backend/
│   ├── main.py              ← API Python (FastAPI)
│   └── requirements.txt     ← Dependências Python
├── frontend/
│   └── index.html           ← Interface web
├── sql/
│   └── pharmapele_schema_completo.sql ← Script do banco de dados (Estrutura Completa)
├── iniciar.bat              ← Iniciar no Windows (duplo clique)
└── README.md
```

---

## 🛠️ Instalação (Windows)

### Passo 1 — Instalar o PostgreSQL

1. Acesse: https://www.postgresql.org/download/windows/
2. Baixe o instalador (versão 16 ou superior)
3. Durante a instalação:
   - Anote a senha que você definir para o usuário `postgres`
   - Porta padrão: **5432** (deixe como está)
4. Instale também o **pgAdmin** (vem junto na instalação)

---

### Passo 2 — Criar o Banco de Dados

1. Abra o **pgAdmin 4**
2. Conecte ao servidor local com usuário `postgres` e sua senha
3. Clique com botão direito em **Databases → Create → Database...**
4. Nome: `pharmapele` → clique OK
5. Clique com botão direito em `pharmapele` → **Query Tool**
6. Abra o arquivo `sql/pharmapele_schema_completo.sql` e execute (**F5**)

---

### Passo 3 — Instalar o Python

1. Acesse: https://www.python.org/downloads/
2. Baixe o Python 3.11 ou superior
3. **IMPORTANTE**: Marque a opção **"Add Python to PATH"** durante a instalação
4. Conclua a instalação

---

### Passo 4 — Configurar a Senha do Banco

Abra o arquivo `iniciar.bat` com o Bloco de Notas e edite as linhas:

```bat
if "%DB_PASS%"=="" set DB_PASS=postgres
```

Troque `postgres` pela senha que você definiu no PostgreSQL.

Ou, alternativamente, abra o Prompt de Comando e defina antes de iniciar:
```cmd
set DB_PASS=sua_senha_aqui
iniciar.bat
```

---

### Passo 5 — Iniciar o Sistema

Dê **duplo clique** no arquivo `iniciar.bat`.

Na primeira vez, ele instala automaticamente as dependências Python.

Depois, abra o navegador em: **http://localhost:8000**

---

## 🚀 Como Usar

### Usuários de Teste (Níveis de Acesso)

O banco de dados é inicializado com perfis de teste pré-configurados para simulação de permissões (todos compartilham a senha: `Ojuara10*`):

*   **Administrador Master (Matriz):** `admin@pharmapele.com.br` — Acesso total a todas as franquias, lojas e gerenciamento de usuários.
*   **Franqueado:** `franqueado@pharmapele.com.br` — Visualiza e gerencia apenas as lojas vinculadas à sua franquia específica.
*   **Operador de Loja:** `operador@pharmapele.com.br` — Visualização restrita a uma única loja específica.

### Importar Notas Fiscais
1. Acesse **Importar XMLs** no menu lateral
2. Arraste um ou vários arquivos `.xml` de NFC-e para a área indicada
3. O sistema processará e salvará automaticamente no banco

### Dashboard
- Faturamento total e do mês atual
- Gráfico mensal de receita
- Produtos mais vendidos
- Alertas de recompra próximos

### Alertas de Recompra
- O sistema calcula automaticamente o intervalo médio de compra por cliente/produto
- Exibe quem está perto de precisar recomprar
- Botão direto para abrir o WhatsApp com mensagem pré-formatada

### Clientes Inativos
- Lista clientes sem compras há mais de 60 dias
- Link direto para WhatsApp de recuperação

### Mensagens WhatsApp
- Templates prontos para: recompra, recuperação de inativo, fidelidade
- Lista de contatos prioritários do dia com botão de envio direto

---

## ⚙️ Configurações Avançadas

### Variáveis de ambiente (opcional)

| Variável   | Padrão    | Descrição               |
|------------|-----------|-------------------------|
| DB_HOST    | localhost | Endereço do PostgreSQL  |
| DB_PORT    | 5432      | Porta do PostgreSQL     |
| DB_NAME    | pharmapele | Nome do banco           |
| DB_USER    | postgres  | Usuário do banco        |
| DB_PASS    | postgres  | Senha do banco          |

---

## 🔮 Próximas Etapas (Roadmap)

- [ ] Integração com Evolution API para disparo automático de WhatsApp
- [ ] Importação em lote de pasta inteira de XMLs
- [ ] Relatórios em PDF exportáveis
- [ ] Sugestão de cross-sell (produtos complementares)
- [ ] Análise de sazonalidade
- [ ] Painel de metas mensais

---

## ❓ Problemas Comuns

**"Não foi possível conectar ao servidor"**
→ Verifique se o `iniciar.bat` está rodando e se o PostgreSQL está ligado.

**"Erro ao importar XML"**
→ Confirme que o arquivo é uma NFC-e válida (modelo 65).

**"Nenhum alerta de recompra"**
→ O cálculo exige ao menos 2 compras do mesmo produto pelo mesmo cliente.
