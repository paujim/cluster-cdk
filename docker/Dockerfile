FROM docker.elastic.co/elasticsearch/elasticsearch:6.8.6
ADD elasticsearch.yml /usr/share/elasticsearch/config/
USER root
RUN chown elasticsearch:elasticsearch config/elasticsearch.yml
USER elasticsearch
WORKDIR /usr/share/elasticsearch
RUN bin/elasticsearch-plugin install discovery-ec2 -b && bin/elasticsearch-plugin install repository-s3 -b && sed -e '/^-Xm/s/^/#/g' -i /usr/share/elasticsearch/config/jvm.options
EXPOSE 9200 9300

