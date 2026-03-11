import re
import json
import requests
from google import genai

def limpar_texto_ia(texto):
    try: 
        texto = re.sub(r'\\u([0-9a-fA-F]{4})', lambda m: chr(int(m.group(1), 16)), texto)
    except Exception: 
        pass
        
    # Remove asteriscos simples (itálicos chatos) mas preserva os duplos (negritos)
    texto = re.sub(r'(?<!\*)\*(?!\*)', '', texto)
    
    # Dicionário de limpeza extrema
    substituicoes = {
        r'\bmomentum\b': 'impulso',
        r'\blocus\b': 'ambiente',
        r'\boutrossim\b': 'além disso',
        r'\bdessarte\b': 'assim',
        r'\bdestarte\b': 'assim',
        r'\bum mergulho profundo\b': 'uma análise detalhada',
        r'\bmergulho profundo\b': 'análise detalhada',
        r'\btapeçaria de\b': 'conjunto de',
        r'\btapeçaria\b': 'estrutura',
        r'\bfarol\b': 'guia',
        r'\bcrucial\b': 'essencial',
        r'\bvital\b': 'essencial',
        r'\badentrar\b': 'explorar',
        r'\btestamento\b': 'prova',
        r'\bpaisagem\b': 'cenário',
        r'\bnotável\b': 'importante'
    }
    
    for padrao, substituto in substituicoes.items():
        texto = re.sub(padrao, substituto, texto, flags=re.IGNORECASE)
        
    return texto

def calcular_custo_api(modelo, prompt_tokens, completion_tokens):
    usd_to_brl = 5.50
    custo_usd = 0.0
    mod_lower = modelo.lower()
    
    # Tabela de preços exata atualizada (Março/2024+) - Valores por 1 Milhão de Tokens
    if "claude-3.5-sonnet" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 3.0) + (completion_tokens / 1000000 * 15.0)
    elif "claude-3-opus" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 15.0) + (completion_tokens / 1000000 * 75.0)
    elif "gpt-4o-mini" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 0.15) + (completion_tokens / 1000000 * 0.60)
    elif "gpt-4o" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 2.5) + (completion_tokens / 1000000 * 10.0)
    elif "llama-3.3-70b" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 0.4) + (completion_tokens / 1000000 * 0.4)
    elif "qwen" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 0.4) + (completion_tokens / 1000000 * 0.4)
    elif "gemini-2.5-pro" in mod_lower or "gemini-pro" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 1.25) + (completion_tokens / 1000000 * 5.0)
    elif "gemini-2.5-flash" in mod_lower or "gemini-flash" in mod_lower:
        custo_usd = (prompt_tokens / 1000000 * 0.075) + (completion_tokens / 1000000 * 0.3)
        
    return custo_usd * usd_to_brl

def chamar_ia(prompt, nome_modelo, chave_google=None, chave_openrouter=None):
    is_openrouter = "openrouter/" in nome_modelo.lower() or "/" in nome_modelo
    custo_reais = 0.0
    
    if is_openrouter:
        if not chave_openrouter: 
            raise Exception("A Chave da API do OpenRouter não foi configurada.")
            
        modelo_limpo = nome_modelo.replace("openrouter/", "")
        headers = {
            "Authorization": f"Bearer {chave_openrouter}",
            "HTTP-Referer": "https://hubmaster-system.com",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": modelo_limpo, 
            "messages": [{"role": "user", "content": prompt}], 
            "temperature": 0.7
        }
        
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions", 
            headers=headers, 
            json=payload, 
            timeout=180
        )
        
        if res.status_code != 200: 
            raise Exception(f"Erro OpenRouter ({res.status_code}): {res.text}")
            
        dados = res.json()
        texto = dados['choices'][0]['message']['content']
        
        usage = dados.get('usage', {})
        custo_reais = calcular_custo_api(modelo_limpo, usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))
        
        return limpar_texto_ia(texto), custo_reais
        
    else:
        if not chave_google: 
            raise Exception("A Chave da API nativa do Google não foi configurada.")
            
        client = genai.Client(api_key=chave_google)
        res = client.models.generate_content(model=nome_modelo, contents=prompt)
        
        try:
            pt = res.usage_metadata.prompt_token_count
            ct = res.usage_metadata.candidates_token_count
            custo_reais = calcular_custo_api(nome_modelo, pt, ct)
        except Exception: 
            pass
            
        return limpar_texto_ia(res.text), custo_reais

def extrair_dicionario(texto_ia):
    chaves = [
        "ASPECTO_1", "POR_QUE_1", "ASPECTO_2", "POR_QUE_2", "ASPECTO_3", "POR_QUE_3", 
        "CONCEITOS_TEORICOS", "ANALISE_CONCEITO_1", "ENTENDIMENTO_TEORICO", "SOLUCOES_TEORICAS", 
        "RESUMO_MEMORIAL", "CONTEXTO_MEMORIAL", "ANALISE_MEMORIAL", "PROPOSTAS_MEMORIAL", 
        "CONCLUSAO_MEMORIAL", "REFERENCIAS_ADICIONAIS", "AUTOAVALIACAO_MEMORIAL"
    ]
    dic = {}
    for chave in chaves:
        # Expressão regular fortificada para lidar com IAs teimosas que formatam errado
        match = re.search(rf"\[START_{chave}\](.*?)(?=\[END_{chave}\]|\[START_|$)", texto_ia, re.DOTALL | re.IGNORECASE)
        if match:
            trecho = match.group(1).strip()
            while trecho.startswith('**') and trecho.endswith('**') and len(trecho) > 4: 
                trecho = trecho[2:-2].strip()
            dic[f"{{{{{chave}}}}}"] = trecho
        else: 
            dic[f"{{{{{chave}}}}}"] = "" 
    return dic

def extrair_json_seguro(texto):
    try:
        match = re.search(r'\[.*\]', texto, re.DOTALL)
        if match: 
            return json.loads(match.group(0))
        return json.loads(texto)
    except Exception: 
        return []

def consultar_gasto_openrouter(chave_openrouter):
    """Bate na API da OpenRouter e puxa exatamente os dólares gastos na conta."""
    try:
        if not chave_openrouter: return 0.0
        headers = {"Authorization": f"Bearer {chave_openrouter}"}
        res = requests.get("https://openrouter.ai/api/v1/auth/key", headers=headers, timeout=10)
        if res.status_code == 200:
            dados = res.json()
            # Retorna o uso total em dólares (USD)
            return float(dados.get("data", {}).get("usage", 0.0))
        return 0.0
    except Exception:
        return 0.0
