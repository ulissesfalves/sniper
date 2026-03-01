# infra/nginx/ssl/

Esta pasta é montada pelo container nginx como volume de leitura.
Precisa existir mesmo que vazia — sem ela o docker compose up falha.

Em desenvolvimento local (sem HTTPS):
  Deixe vazia. O nginx.conf não referencia certificados SSL por padrão.

Em produção (com HTTPS):
  Coloque aqui:
    - fullchain.pem   (certificado + cadeia)
    - privkey.pem     (chave privada)
  
  Gerado com Let's Encrypt + certbot:
    certbot certonly --standalone -d seudominio.com
    cp /etc/letsencrypt/live/seudominio.com/fullchain.pem infra/nginx/ssl/
    cp /etc/letsencrypt/live/seudominio.com/privkey.pem   infra/nginx/ssl/

NUNCA commitar arquivos .pem nesta pasta — .gitignore já os exclui.
