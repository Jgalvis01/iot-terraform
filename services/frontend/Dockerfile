FROM nginx:1.25-alpine
COPY services/frontend/index.html services/frontend/styles.css services/frontend/app.js /usr/share/nginx/html/
COPY services/frontend/default.conf.template /etc/nginx/templates/default.conf.template
EXPOSE 80
