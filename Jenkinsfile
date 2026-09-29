// 全流水线:GitHub 推送 → Jenkins → 构建 amd64 镜像 → K3d 测试 Pod(pytest 冒烟) →
//                          构建前端并发布云端静态目录 → Ansible 部署云端 → 公网冒烟 → 清理
//
// 文件位置:prepilot_devops_kit/Jenkinsfile(本仓库根)
// 依赖挂载(见 jenkins/docker-compose.jenkins.yml):
//   应用源码   /srv/prepilot_fastapi   (构建上下文,且需是 git 仓库,stage1 靠 git pull 更新)
//   devops kit /srv/kit/ansible,/srv/kit/k8s (ansible + k8s 清单,只读)
//   kubeconfig /var/jenkins_home/.kube/config (连本地 K3d)
//   ssh        /var/jenkins_home/.ssh       (Ansible 经 Tailscale 连云端)
//
// GitHub 触发:Jenkins 任务勾 "Build when a change is pushed to GitHub",
// 本机无公网 IP,用 Tailscale Funnel 暴露 8080 接收 webhook(详见 docs/webhook-jenkins-setup.md)。
// webhook 只负责触发;流水线自己 git pull 最新代码(见 stage 1)。

pipeline {
  agent any
  environment {
    REGISTRY = '100.82.117.31:5000'
    IMAGE    = 'fastapi-app'
    TAG      = "build-${BUILD_ID}"      // amd64,推 registry,云端 Ansible 部署用
    TEST_TAG = "test-${BUILD_ID}"       // 原生 arm64,仅本地 k3d 测试用,不推 registry
    APP_DIR  = '/srv/prepilot_fastapi'
    KIT_DIR  = '/srv/kit'
    TEST_NS  = "prepilot-test-${BUILD_ID}"
  }
  stages {
    stage('1 拉取最新代码') {
      steps {
        // webhook 仅触发,真正更新靠 pull 本地挂载的工作副本
        sh "git -C ${APP_DIR} pull --ff-only || true"
      }
    }

    stage('2 构建 & 推送镜像') {
      steps {
        // 2a) 云端是 amd64,必须构建 amd64 镜像并推送到 registry(Ansible 部署云端用 :TAG)
        sh "docker build --platform linux/amd64 -t ${REGISTRY}/${IMAGE}:${TAG} ${APP_DIR}"
        sh "docker push ${REGISTRY}/${IMAGE}:${TAG}"

        // 2b) 本地 k3d 节点是 arm64(M1/OrbStack)。单独构建一份【原生 arm64】镜像只用于本地
        //     pytest,不推 registry;直接 import 进节点,彻底规避 "amd64 镜像在 arm64 节点上 ctr
        //     import 报 no match for platform" 的问题(原生 arch 与节点一致,导入即干净解包)。
        //     不推 :latest / :TEST_TAG,确保 IfNotPresent 一定用本地导入的镜像,绝不回退去拉 registry。
        sh "docker build -t ${REGISTRY}/${IMAGE}:${TEST_TAG} ${APP_DIR}"
        sh "docker save ${REGISTRY}/${IMAGE}:${TEST_TAG} -o /tmp/${IMAGE}-${BUILD_ID}.tar"
      }
    }

    stage('3 K3d 测试(pytest 冒烟)') {
      steps {
        sh "kubectl create namespace ${TEST_NS} || true"
        // 把 Python 测试脚本做成 ConfigMap 挂进测试 Job(幂等:已有则更新)
        sh "kubectl -n ${TEST_NS} create configmap prepilot-test-script " +
           "--from-file=test_api.py=${KIT_DIR}/k8s/test/test_api.py " +
           "--dry-run=client -o yaml | kubectl -n ${TEST_NS} apply -f -"

        // 关键:把刚构建的原生 arm64 测试镜像直接导入 k3d 两个节点的 containerd,再 apply,
        // 避免节点去 registry 拉取。100.82.117.31 是 Mac 的 Tailscale IP,k3d 节点(OrbStack 内)
        // 未必能路由到/信任该 insecure registry。Jenkins 容器有 docker socket,用 docker cp ->
        // 节点内【裸 ctr】(containerd CLI)+ 显式 k3s socket(/run/k3s/containerd/containerd.sock)
        // 与 k8s.io 命名空间。注意【不能】用 "k3s ctr" —— docker exec 下 k3s 子命令分发失败,
        // 报 "No help topic for 'ctr'"。原生 arm64 与节点 arch 一致,裸 ctr import 即可干净解包;
        // 中途偶有 "content digest ... not found" 的 ERRO 行属正常(层异步写入),末行
        // Successfully imported + 退出码 0 即可。
        sh "docker cp /tmp/${IMAGE}-${BUILD_ID}.tar k3d-dev-cluster-server-0:/tmp/"
        sh "docker exec k3d-dev-cluster-server-0 ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import /tmp/${IMAGE}-${BUILD_ID}.tar"
        sh "docker cp /tmp/${IMAGE}-${BUILD_ID}.tar k3d-dev-cluster-agent-0:/tmp/"
        sh "docker exec k3d-dev-cluster-agent-0 ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import /tmp/${IMAGE}-${BUILD_ID}.tar"
        sh "rm -f /tmp/${IMAGE}-${BUILD_ID}.tar || true"

        // 被测服务(Deployment+Service) + 测试 Job 一起 apply(此时镜像已在节点本地)
        sh "kubectl -n ${TEST_NS} apply -f ${KIT_DIR}/k8s/test/"
        // 把 Deployment 镜像从清单里的 :latest 改成这次导入的原生 arm64 :TEST_TAG,并强制
        // IfNotPresent:确保测试 Pod 用本地导入的镜像,绝不从 registry 拉(:TEST_TAG 本来就不在 registry)。
        sh "kubectl -n ${TEST_NS} patch deployment prepilot-app -p '{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"app\",\"image\":\"${REGISTRY}/${IMAGE}:${TEST_TAG}\",\"imagePullPolicy\":\"IfNotPresent\"}]}}}}'"

        // 等 Deployment 就绪(镜像已在节点本地)
        sh "kubectl -n ${TEST_NS} rollout status deployment/prepilot-app --timeout=180s"
        // 等测试 Job 跑完(脚本内部已重试等 app 就绪;失败则 Job 非 0 退出 -> 此处超时失败)
        // 超时给 600s 余量:Job 内要先 pip 装 pytest,官方源实测 113s,叠加调度曾超 300s 而失败;
        // 现 test-job.yaml 已指定清华镜像(实测从创建到 Complete 仅 9s),余量用于防网络抖动。
        sh "kubectl -n ${TEST_NS} wait --for=condition=complete job/prepilot-test --timeout=600s"
        // 判定到此为止:Job 的 condition=complete 即 pytest 全绿(失败则 wait 超时报错)。
        // 不再取日志文本:本环境 API server 代理 kubelet 持续 502,拿不到输出且噪音大。
      }
    }

    stage('4 构建前端并发布云端') {
      environment {
        // Jenkins 镜像内没有 Node：装到 jenkins_home 卷里，只首次下载，后续构建直接复用
        NODE_VERSION = 'v22.22.2'
        NODE_HOME    = '/var/jenkins_home/tools/node'
        NPM_REGISTRY = 'https://registry.npmmirror.com'
        FRONTEND_DIR = '/srv/prepilot_fastapi/frontend'
        CLOUD_HOST   = '100.116.132.10'                  // 云端 Tailscale 内网 IP
        CLOUD_DIR    = '/opt/prepilot_cloud/frontend_dist' // nginx 静态根目录(见 compose 挂载)
        SSH_KEY      = '/var/jenkins_home/.ssh/id_ed25519'
      }
      steps {
        sh '''
          set -eo pipefail

          # 1) 准备 Node 运行时
          #    为什么不在 Jenkins 镜像里装:镜像重建成本高;为什么不 docker run node 镜像:
          #    本机 docker daemon 拉不动 registry-1.docker.io(实测 Bad Gateway),故改用
          #    npmmirror 二进制镜像下载官方 tarball,落地到持久卷 /var/jenkins_home/tools/node。
          if [ ! -x "$NODE_HOME/bin/node" ]; then
            echo ">>> 首次安装 Node $NODE_VERSION"
            mkdir -p "$NODE_HOME"
            curl -fsSL "https://registry.npmmirror.com/-/binary/node/$NODE_VERSION/node-$NODE_VERSION-linux-arm64.tar.gz" -o /tmp/node-$NODE_VERSION.tar.gz
            tar -xzf /tmp/node-$NODE_VERSION.tar.gz -C "$NODE_HOME" --strip-components=1
            rm -f /tmp/node-$NODE_VERSION.tar.gz
          fi
          export PATH="$NODE_HOME/bin:$PATH"
          node -v && npm -v

          # 2) 安装依赖并构建(npm cache 落在持久卷,二次构建明显变快)
          cd "$FRONTEND_DIR"
          npm ci --registry="$NPM_REGISTRY" --cache /var/jenkins_home/.npm < /dev/null
          npm run build < /dev/null
          # 容器内是 uid 1000,放宽权限,避免本机(uid 501)后续 npm 操作撞 EACCES
          chmod -R a+rwX node_modules 2>/dev/null || true

          # 3) 发布到云端:清空目录内容后 tar 灌入。以下几条都是踩过的坑,别改回去:
          #    ① 不能 rm -rf 目录本身再 mkdir —— bind mount 按 inode 挂,目录一删一建,
          #       nginx 容器里挂的还是旧 inode 的“空目录”,结果首页 403。用 find -mindepth 1
          #       -delete 只清内容、保 inode。
          #    ② 不能 rm -rf dir/* —— 星号匹配不到点文件,历史 ._* 垃圾会残留。
          #    ③ 不用 scp -r —— scp 走 stdin 协议,在 Jenkins sh 步骤里易与 stdin 争用导致
          #       静默中断(什么都没传、还返回 0)。tar 管道只打包 dist 内容,更稳更干净。
          ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=no root@$CLOUD_HOST \\
              "mkdir -p $CLOUD_DIR && find $CLOUD_DIR -mindepth 1 -delete"
          tar -czf - -C ./dist . | ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no root@$CLOUD_HOST \\
              "tar -xzf - -C $CLOUD_DIR"
          #    ④ 末尾 restart 一次 nginx:若历史上有人 rm -rf 过整个目录,bind mount 会挂到旧
          #       inode 的空目录上,nginx 永远看不到新文件(表现就是首页 403)。restart 会重新
          #       解析挂载源,成本 1 秒,权当兜底。
          ssh -n -i "$SSH_KEY" -o StrictHostKeyChecking=no root@$CLOUD_HOST \\
              "docker restart prepilot-nginx >/dev/null && sleep 2; ls -1 $CLOUD_DIR; du -sh $CLOUD_DIR"
        '''
      }
    }

    stage('5 Ansible 部署云端') {
      steps {
        // 走 Tailscale 内网 100.116.132.10,用挂载的 SSH key 驱动 docker compose pull && up -d
        sh "ansible-playbook -i ${KIT_DIR}/ansible/inventory/hosts.yml " +
           "${KIT_DIR}/ansible/playbooks/deploy-prepilot.yml -e image_tag=${TAG}"
      }
    }

    stage('6 云端公网冒烟') {
      steps {
        // 经 nginx(80)走完整链路:公网 /api/health -> nginx -> api:8000/health
        sh "curl -f http://182.254.244.139/api/health"
        // 前端首页:nginx 静态目录 -> 确认 SPA 产物已随流水线发布
        sh "curl -f http://182.254.244.139/ | grep -q 'id=\"root\"'"
      }
    }
  }
  post {
    always {
      // 每次构建独立命名空间,无论成败都清理,避免残留
      sh "kubectl delete namespace ${TEST_NS} --ignore-not-found"
    }
  }
}
