sudo yum -y update && sudo sysctl -w vm.max_map_count=262144
mkdir -p /usr/share/elasticsearch/data/
chown -R 1000.1000 /usr/share/elasticsearch/data/
sudo mount /dev/xvdb /usr/share/elasticsearch/data/
