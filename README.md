# Random Photos • For Wallpaper Engine

![App Logo](icon.png) 

README Gerado automaticamente  
Um utilitário desenvolvido em **Python + PySide6** que automatiza a troca de imagens e vídeos no **Wallpaper Engine**.  
O programa permite configurar múltiplos monitores, alternar wallpapers em intervalos definidos, aplicar efeitos de *fade* suave e salvar diferentes perfis de configuração.

---

## ✨ Funcionalidades

- Suporte a múltiplos monitores.  
- Suporte a imagens (`.png`, `.jpg`, `.jpeg`, `.gif`, `.bmp`) e vídeos (`.mp4`).  
- Alternância automática por tempo configurável.  
- Opção de **shuffle** (ordem aleatória).  
- Efeito de *fade in/out* suave ao trocar wallpapers.  
- Ícone na bandeja do sistema para rodar em segundo plano.  
- Configurações salvas em JSON para reutilizar depois.  
- Prevenção de múltiplas instâncias (não abre mais de uma vez).  

---

## 📦 Instalação

1. Certifique-se de ter o **Wallpaper Engine** instalado e rodando.  
2. Baixe a versão compilada (`.exe`) ou rode direto via Python:
   ```bash
   pip install PySide6
   python main.py
   ```
3. Ao empacotar para distribuição use:
   ```bash
   pyinstaller --onefile --windowed --icon=icon.ico main.py
   ```

---

## 🚀 Como usar

### 1. Adicionar um monitor
- Clique em **Add Monitor**.  
- Defina o número do monitor (ex.: `1`, `2`...).  

### 2. Campos principais
| Campo                        | O que faz                                                                 |
|------------------------------|---------------------------------------------------------------------------|
| **Wallpaper Engine executable** | Caminho para o `wallpaper32.exe` ou `wallpaper64.exe` do Wallpaper Engine. |
| **Monitor**                  | Número do monitor que receberá os wallpapers.                            |
| **Interval (s)**             | Intervalo em segundos entre cada troca de wallpaper.                      |
| **Enable fade**              | Ativa/desativa o efeito de fade suave.                                    |
| **Fade name**                | Nome da propriedade de fade (em geral `opaimg`).                          |
| **Fade step**                | Incremento usado para suavizar o fade (quanto menor, mais suave).         |
| **Shuffle images**           | Embaralha a ordem das imagens em vez de seguir sequência.                  |
| **Extensions**               | Extensões aceitas para arquivos (separe por vírgula).                     |
| **Props (key=path, 1 per line)** | Lista de propriedades do Wallpaper Engine. Exemplo: <br> `_11=C:/Users/User/Pictures` <br> `_169=C:/Wallpapers`. |

### 3. Botões principais
- **Start** → inicia a troca automática de wallpapers.  
- **Stop** → interrompe o processo (aplicando o fade final).  
- **Load / Save** → carregar ou salvar configurações JSON.  
- **Autoplay** → se marcado, o app inicia direto em segundo plano.  
- **About** → mostra informações do app.  

---

## 📂 Estrutura de configuração (JSON)

Exemplo de arquivo salvo:
```json
{
  "autoplay": true,
  "monitors": [
    {
      "exe_path": "C:/Program Files (x86)/Steam/steamapps/common/wallpaper_engine/wallpaper32.exe",
      "monitor": "1",
      "props": {
        "_11": "C:/Users/User/Pictures/Wallpapers",
        "_169": "D:/Videos/Loops"
      },
      "passo_fade": "0.20",
      "intervalo_segundos": 1800,
      "aleatorio": true,
      "fade": true,
      "fadename": "opaimg",
      "extensoes": [".png", ".jpg", ".jpeg", ".gif", ".bmp", ".mp4"]
    }
  ]
}
```

---

## 🖼️ Demonstração

### Interface principal
![Screenshot](docs/screenshot.png)  
*(coloque aqui uma captura estática da janela principal)*

### Funcionamento em tempo real
![Demo GIF](docs/demo.gif)  
*(coloque aqui um GIF mostrando o fade/shuffle em ação)*

---

## ⚠️ Requisitos

- Windows 10/11  
- Wallpaper Engine (rodando)  
- Python 3.9+ (se usar versão em código)  

---

## 👨‍💻 Autor

**Rafael Neves**  
🌐 [rafaelneves.dev.br](https://rafaelneves.dev.br)  

---

## 📜 Licença

Este projeto é distribuído sob a licença MIT.  
Sinta-se livre para usar, modificar e compartilhar.
