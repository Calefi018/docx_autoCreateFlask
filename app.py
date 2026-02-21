import os
import io
import re
import base64
import traceback
from datetime import datetime, date
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from docx import Document
import google.generativeai as genai

app = Flask(__name__)

# =========================================================
# CONFIGURAÇÕES DO BANCO DE DADOS E SEGURANÇA
# =========================================================
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'chave-super-secreta-mude-depois')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL', 'sqlite:///clientes.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "Por favor, faça login para acessar a ferramenta."

CHAVE_API = os.environ.get("GEMINI_API_KEY")
if CHAVE_API:
    genai.configure(api_key=CHAVE_API)

# =========================================================
# MODELO DO BANCO DE DADOS
# =========================================================
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='cliente') # 'admin', 'sub-admin', 'cliente'
    expiration_date = db.Column(db.Date, nullable=True)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# Cria o banco de dados e o Admin Master ao iniciar o sistema
with app.app_context():
    db.create_all()
    if not User.query.filter_by(username='admin').first():
        hashed_pw = generate_password_hash('admin123')
        admin = User(username='admin', password=hashed_pw, role='admin')
        db.session.add(admin)
        db.session.commit()

# =========================================================
# FUNÇÕES DA IA E MANIPULAÇÃO DE WORD
# =========================================================
def preencher_template_com_tags(arquivo_template, dicionario_dados):
    doc = Document(arquivo_template)
    def processar_paragrafo(paragrafo):
        texto_original = paragrafo.text
        tem_tag = False
        for marcador, texto_novo in dicionario_dados.items():
            if marcador in texto_original:
                texto_original = texto_original.replace(marcador, str(texto_novo))
                tem_tag = True
        if tem_tag:
            titulos_memorial = ["Resumo", "Contextualização do desafio", "Análise", "Propostas de solução", "Conclusão reflexiva", "Referências", "Autoavaliação"]
            for t in titulos_memorial:
                if texto_original.strip().startswith(t):
                    texto_original = texto_original.replace(t, f"**{t}**\n", 1)
            titulos_aspectos = ["Aspecto 1:", "Aspecto 2:", "Aspecto 3:", "Por quê:"]
            for t in titulos_aspectos:
                if t in texto_original:
                    if "Por quê:" in t:
                        texto_original = texto_original.replace(t, f"\n**{t}** ")
                    else:
                        texto_original = texto_original.replace(t, f"**{t}** ")
            if "?" in texto_original and "Por quê:" not in texto_original:
                partes = texto_original.split("?", 1)
                pergunta = partes[0].strip()
                if 10 < len(pergunta) < 150 and not pergunta.startswith("**"):
                    texto_original = f"**{pergunta}?**\n" + partes[1].lstrip()
            texto_original = texto_original.replace("**\n ", "**\n").replace(":**\n:", ":**\n")
            paragrafo.clear()
            linhas = texto_original.split('\n')
            for i, linha in enumerate(linhas):
                partes = linha.split('**')
                for j, parte in enumerate(partes):
                    if parte: 
                        run = paragrafo.add_run(parte)
                        if j % 2 == 1: run.bold = True
                if i < len(linhas) - 1: paragrafo.add_run('\n')

    for paragrafo in doc.paragraphs: processar_paragrafo(paragrafo)
    for tabela in doc.tables:
        for linha in tabela.rows:
            for celula in linha.cells:
                for paragrafo in celula.paragraphs: processar_paragrafo(paragrafo)

    arquivo_saida = io.BytesIO()
    doc.save(arquivo_saida)
    arquivo_saida.seek(0)
    return arquivo_saida

def gerar_respostas_ia_tags(texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Você é um aluno universitário inteligente, estudioso e objetivo resolvendo um Desafio Profissional.
    
    REGRA DE OURO (LINGUAGEM HUMANA):
    - É EXPRESSAMENTE PROIBIDO usar palavras robóticas de IA (ex: "multifacetado", "tessitura", "arcabouços", "mergulhar", "jornada", "adentrar", "imperativo", "em suma"). 
    - Escreva de forma natural, direta e acadêmica, como um estudante real.
    
    REGRA DE ESTRUTURA E LIMITES (MUITO IMPORTANTE):
    - NÃO USE FORMATO JSON. Retorne o texto preenchendo as caixas delimitadoras exatas abaixo.
    - É ESTRITAMENTE PROIBIDO usar frases introdutórias (como "Aqui estão as respostas").
    - Para destacar conceitos, use **negrito**. Para tópicos, use o traço (-).
    - LIMITE DE PARÁGRAFOS ESTRITO: Respeite rigorosamente os limites de tamanho indicados nas caixas abaixo. Se a regra pede "1 parágrafo", gere EXATAMENTE UM ÚNICO PARÁGRAFO SEM QUEBRAS DE LINHA (Enter) no meio.
    - O texto total gerado para as seções do Memorial Analítico NÃO PODE passar de 5500 caracteres.
    - É EXPRESSAMENTE PROIBIDO ATRIBUIR NOTAS NUMÉRICAS A SI MESMO NA AUTOAVALIAÇÃO.
    
    TEMA/CASO DO DESAFIO (com as referências no final):
    {texto_tema}
    
    GERAÇÃO OBRIGATÓRIA (Crie textos dentro de cada delimitador respeitando as regras acima e nada mais):
    
    [START_ASPECTO_1] Descreva o aspecto 1 de forma técnica e profunda... [END_ASPECTO_1]
    [START_POR_QUE_1] Justifique o aspecto 1 com uma análise densa de pelo menos 4 linhas... [END_POR_QUE_1]
    [START_ASPECTO_2] Descreva o aspecto 2 de forma técnica... [END_ASPECTO_2]
    [START_POR_QUE_2] Justifique o aspecto 2 com uma análise densa de pelo menos 4 linhas... [END_POR_QUE_2]
    [START_ASPECTO_3] Descreva o aspecto 3 de forma técnica... [END_ASPECTO_3]
    [START_POR_QUE_3] Justifique o aspecto 3 com uma análise densa de pelo menos 4 linhas... [END_POR_QUE_3]
    [START_CONCEITOS_TEORICOS] - **[Nome do Conceito 1]:** [Explicação teórica detalhada sobre como se aplica ao caso]\n- **[Nome do Conceito 2]:** [Explicação teórica detalhada...] [END_CONCEITOS_TEORICOS]
    [START_ANALISE_CONCEITO_1] Análise teórica profunda respondendo como o conceito principal explica o que aconteceu na situação... [END_ANALISE_CONCEITO_1]
    [START_ENTENDIMENTO_TEORICO] Análise teórica densa respondendo o que a teoria ajuda a entender sobre o problema central... [END_ENTENDIMENTO_TEORICO]
    [START_SOLUCOES_TEORICAS] Apresente um plano de ação robusto respondendo que soluções possíveis a teoria aponta e por que fazem sentido... [END_SOLUCOES_TEORICAS]
    [START_RESUMO_MEMORIAL] Escreva EXATAMENTE 1 (um) parágrafo resumindo o que descobriu no caso. [END_RESUMO_MEMORIAL]
    [START_CONTEXTO_MEMORIAL] Escreva EXATAMENTE 1 (um) parágrafo contextualizando (Quem? Onde? Qual a situação?). [END_CONTEXTO_MEMORIAL]
    [START_ANALISE_MEMORIAL] Escreva EXATAMENTE 1 (um) parágrafo usando 2 a 3 conceitos da disciplina para explicar a situação com exemplos do caso. [END_ANALISE_MEMORIAL]
    [START_PROPOSTAS_MEMORIAL] Escreva no MÁXIMO 2 (dois) parágrafos com propostas de solução. O que você recomenda? Por quê? Qual teoria apoia? [END_PROPOSTAS_MEMORIAL]
    [START_CONCLUSAO_MEMORIAL] Escreva no MÁXIMO 2 (dois) parágrafos de conclusão reflexiva. O que você aprendeu com essa experiência? [END_CONCLUSAO_MEMORIAL]
    [START_REFERENCIAS_ADICIONAIS] Localize as referências bibliográficas e fontes que foram informadas no texto do TEMA e liste-as rigorosamente no padrão ABNT. [END_REFERENCIAS_ADICIONAIS]
    [START_AUTOAVALIACAO_MEMORIAL] Escreva EXATAMENTE 1 (um) parágrafo em primeira pessoa ("eu"). Reflita sobre o que você percebeu sobre seu próprio processo de estudo. É EXPRESSAMENTE PROIBIDO citar inteligência artificial, regras de formatação ou dar qualquer nota/pontuação a si mesmo. [END_AUTOAVALIACAO_MEMORIAL]
    """
    try:
        resposta = modelo.generate_content(prompt)
        chaves = ["ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"]
        dicionario_higienizado = {}
        for chave in chaves:
            match = re.search(rf"\[START_{chave}\](.*?)\[END_{chave}\]", resposta.text, re.DOTALL)
            dicionario_higienizado[f"{{{{{chave}}}}}"] = match.group(1).strip() if match else "" 
        return dicionario_higienizado
    except Exception as e:
        raise Exception(f"Falha de geração na IA: {str(e)}")

def gerar_resolucao_inteligente_gabarito(texto_template, texto_tema, nome_modelo):
    modelo = genai.GenerativeModel(nome_modelo)
    prompt = f"""
    Atue como um aluno universitário estudioso resolvendo um Desafio Profissional.
    TEMA/CASO (com as referências no final): {texto_tema}
    TEMPLATE: {texto_template}
    
    REGRA MÁXIMA DE COMPORTAMENTO E QUALIDADE:
    - NÃO use NENHUMA saudação ou despedida. Vá DIRETO AO PONTO.
    - LINGUAGEM HUMANA: É expressamente proibido usar palavras robóticas de IA (ex: "multifacetado", "tessitura", "arcabouços", "mergulhar").
    - LIMITE DE PARÁGRAFOS ESTRITO (ETAPA 5): Respeite os limites exigidos nas rubricas (ex: 1 parágrafo para Resumo e Análise, Máximo de 2 para Conclusão). O texto total não pode passar de 5500 caracteres.
    - É EXPRESSAMENTE PROIBIDO ATRIBUIR NOTAS NUMÉRICAS A SI MESMO NA AUTOAVALIAÇÃO.
    
    ESTRUTURA VISUAL OBRIGATÓRIA (SIGA ESTE PADRÃO MARKDOWN):
    
    Pré-visualização do Resultado:
    Olá! Serei seu especialista acadêmico. Vamos preencher o template passo a passo.
    
    ---
    **Na Etapa 1, você deve apenas ler e compreender o desafio.**
    
    ---
    **Na Etapa 2 (Materiais de referência), escreva isso:**
    
    **1. O que chamou atenção:** **[Aspecto 1]**
    - **Por quê:** [Justificativa de no mínimo 4 linhas]
    
    **2. O que chamou atenção:** **[Aspecto 2]**
    - **Por quê:** [Justificativa]
    
    **3. O que chamou atenção:** **[Aspecto 3]**
    - **Por quê:** [Justificativa]
    
    ---
    **Na Etapa 3 (Levantamento de conceitos), escreva isso:**
    
    - **[Nome do Conceito 1]:** [Definição]
    - **[Nome do Conceito 2]:** [Definição]
    
    ---
    **Na Etapa 4 (Aplicação dos conceitos), escreva isso:**
    
    - **Como o conceito explica o que aconteceu?**
      [Parágrafo analítico]
    - **O que a teoria ajuda a entender sobre o problema?**
      [Parágrafo conectando sintomas e teorias]
    - **Que soluções a teoria aponta?**
      [Propostas práticas detalhadas]
      
    ---
    **Na Etapa 5 (Memorial Analítico), escreva isso:**
    
    **Resumo do que você descobriu:** [EXATAMENTE 1 Parágrafo]
    **Contextualização do desafio:** [EXATAMENTE 1 Parágrafo: Quem? Onde? Qual a situação?]
    **Análise:** [EXATAMENTE 1 Parágrafo utilizando conceitos]
    **Propostas de solução:** [MÁXIMO 2 Parágrafos com recomendações]
    **Conclusão reflexiva:** [MÁXIMO 2 Parágrafos sobre o que aprendeu]
    **Referências:** [Extraia as referências do texto do tema e formate em padrão ABNT]
    **Autoavaliação:** [EXATAMENTE 1 Parágrafo em primeira pessoa sobre o processo de estudo. NUNCA DÊ UMA NOTA A SI MESMO]
    """
    try:
        return modelo.generate_content(prompt).text
    except Exception as e:
        raise Exception(f"Falha na IA (Gabarito): {str(e)}")

# =========================================================
# ROTAS DE AUTENTICAÇÃO E ADMINISTRAÇÃO
# =========================================================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form.get('username')).first()
        if user and check_password_hash(user.password, request.form.get('password')):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('Usuário ou senha incorretos.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/mudar_senha', methods=['GET', 'POST'])
@login_required
def mudar_senha():
    if request.method == 'POST':
        senha_atual = request.form.get('senha_atual')
        nova_senha = request.form.get('nova_senha')
        
        if check_password_hash(current_user.password, senha_atual):
            current_user.password = generate_password_hash(nova_senha)
            db.session.commit()
            flash('Sua senha foi atualizada com sucesso!', 'success')
            return redirect(url_for('index'))
        else:
            flash('A senha atual está incorreta.', 'error')
            
    return render_template('mudar_senha.html')

@app.route('/admin', methods=['GET', 'POST'])
@login_required
def admin():
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
        
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role')
        exp_date_str = request.form.get('expiration_date')
        
        # Trava: Sub-admins não podem criar novos admins ou sub-admins
        if current_user.role == 'sub-admin' and role != 'cliente':
            flash('Sub-admins só podem criar contas de nível Cliente.', 'error')
            return redirect(url_for('admin'))

        if User.query.filter_by(username=username).first():
            flash('Este nome de usuário já existe!', 'error')
        else:
            hashed_pw = generate_password_hash(password)
            exp_date = datetime.strptime(exp_date_str, '%Y-%m-%d').date() if exp_date_str else None
            new_user = User(username=username, password=hashed_pw, role=role, expiration_date=exp_date)
            db.session.add(new_user)
            db.session.commit()
            flash('Usuário criado com sucesso!', 'success')
            
    users = User.query.all()
    return render_template('admin.html', users=users, hoje=date.today())

@app.route('/edit_user/<int:id>', methods=['GET', 'POST'])
@login_required
def edit_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    
    user_to_edit = User.query.get_or_404(id)
    
    # Trava: Sub-admin só pode editar quem for 'cliente'
    if current_user.role == 'sub-admin' and user_to_edit.role != 'cliente':
        flash('Acesso negado: Você só tem permissão para editar contas de clientes.', 'error')
        return redirect(url_for('admin'))
        
    if request.method == 'POST':
        # 1. Alteração de Senha (Admin e Sub-admin podem, dentro das regras)
        nova_senha = request.form.get('password')
        if nova_senha:
            user_to_edit.password = generate_password_hash(nova_senha)
            
        # 2. Alteração de Data de Expiração
        exp_date_str = request.form.get('expiration_date')
        if exp_date_str:
            user_to_edit.expiration_date = datetime.strptime(exp_date_str, '%Y-%m-%d').date()
        else:
            user_to_edit.expiration_date = None # Deixa vitalício se apagar
            
        # 3. Alteração de Nível/Role (EXCLUSIVO do Admin Master)
        if current_user.role == 'admin':
            novo_nivel = request.form.get('role')
            # Impede o admin master de rebaixar a si mesmo sem querer
            if novo_nivel and user_to_edit.username != 'admin': 
                user_to_edit.role = novo_nivel
                
        db.session.commit()
        flash(f'Dados do usuário {user_to_edit.username} atualizados com sucesso!', 'success')
        return redirect(url_for('admin'))
        
    return render_template('edit_user.html', user=user_to_edit)

@app.route('/delete_user/<int:id>')
@login_required
def delete_user(id):
    if current_user.role not in ['admin', 'sub-admin']: abort(403)
    user_to_delete = User.query.get_or_404(id)
    
    # Trava: Sub-admin só pode apagar cliente
    if current_user.role == 'sub-admin' and user_to_delete.role != 'cliente':
        flash('Acesso negado: Você só tem permissão para apagar contas de clientes.', 'error')
        return redirect(url_for('admin'))
        
    if user_to_delete.username == 'admin':
        flash('Não pode apagar o administrador principal!', 'error')
    else:
        db.session.delete(user_to_delete)
        db.session.commit()
        flash('Usuário apagado!', 'success')
    return redirect(url_for('admin'))

# =========================================================
# ROTAS PRINCIPAIS DA FERRAMENTA (Protegidas)
# =========================================================
@app.route('/')
@login_required
def index():
    if current_user.role == 'cliente' and current_user.expiration_date and date.today() > current_user.expiration_date:
        return render_template('expirado.html')

    modelos_disponiveis = []
    if CHAVE_API:
        try:
            for m in genai.list_models():
                if 'generateContent' in m.supported_generation_methods:
                    modelos_disponiveis.append(m.name.replace('models/', ''))
        except: pass
    if "gemini-1.5-flash" not in modelos_disponiveis: modelos_disponiveis.insert(0, "gemini-1.5-flash")
        
    return render_template('index.html', modelos=modelos_disponiveis, role=current_user.role, user=current_user)

@app.route('/processar', methods=['POST'])
@login_required
def processar():
    if current_user.role == 'cliente' and current_user.expiration_date and date.today() > current_user.expiration_date:
        return jsonify({"erro": "A sua subscrição expirou. Por favor, renove o acesso."}), 403

    try:
        ferramenta = request.form.get('ferramenta')
        modelo_escolhido = request.form.get('modelo')
        texto_tema = request.form.get('tema')
        
        if not texto_tema:
            return jsonify({"erro": "O tema do desafio não foi enviado."}), 400

        caminho_padrao = os.path.join(app.root_path, 'TEMPLATE_COM_TAGS.docx')
        if not os.path.exists(caminho_padrao):
            return jsonify({"erro": "O arquivo TEMPLATE_COM_TAGS.docx não foi encontrado no servidor."}), 400
        
        with open(caminho_padrao, 'rb') as f:
            arquivo_memoria = io.BytesIO(f.read())

        if ferramenta == 'preenchedor':
            respostas_geradas = gerar_respostas_ia_tags(texto_tema, modelo_escolhido)
            if respostas_geradas:
                documento_pronto = preencher_template_com_tags(arquivo_memoria, respostas_geradas)
                arquivo_bytes = documento_pronto.read()
                arquivo_base64 = base64.b64encode(arquivo_bytes).decode('utf-8')
                
                memorial_texto = f"""### Memorial Analítico\n\n**Resumo do que você descobriu**\n{respostas_geradas.get('{{RESUMO_MEMORIAL}}', '')}\n\n**Contextualização do desafio**\n{respostas_geradas.get('{{CONTEXTO_MEMORIAL}}', '')}\n\n**Análise**\n{respostas_geradas.get('{{ANALISE_MEMORIAL}}', '')}\n\n**Propostas de solução**\n{respostas_geradas.get('{{PROPOSTAS_MEMORIAL}}', '')}\n\n**Conclusão reflexiva**\n{respostas_geradas.get('{{CONCLUSAO_MEMORIAL}}', '')}\n\n**Referências**\n{respostas_geradas.get('{{REFERENCIAS_ADICIONAIS}}', '')}\n\n**Autoavaliação**\n{respostas_geradas.get('{{AUTOAVALIACAO_MEMORIAL}}', '')}"""
                return jsonify({"tipo": "sucesso_tags", "arquivo_base64": arquivo_base64, "nome_arquivo": "Desafio_Preenchido.docx", "memorial_texto": memorial_texto})
        elif ferramenta == 'gabarito':
            texto_do_template = extrair_texto_docx(arquivo_memoria)
            resposta_ia = gerar_resolucao_inteligente_gabarito(texto_do_template, texto_tema, modelo_escolhido)
            if resposta_ia: return jsonify({"tipo": "texto", "conteudo": resposta_ia})
                
        return jsonify({"erro": "Opção inválida selecionada."}), 400
    except Exception as e:
        traceback.print_exc()
        return jsonify({"erro": f"Erro interno: {str(e)}"}), 500

if __name__ == '__main__':
    app.run(debug=True)
