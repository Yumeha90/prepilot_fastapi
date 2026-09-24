// 全流水线:GitHub 推送 → Jenkins → 构建 amd64 镜像 → K3d 测试 Pod(pytest 冒烟) →
//                          Ansible 部署云端 → 公网冒烟 → 清理
//
// 文件位置:prepilot_devops_kit/Jenkinsfile(本仓库根)
// 依赖挂载(见 jenkins/docker-compose.jenkins.yml):
//   应用源码   /srv/prepilot_fastapi   (构建上下文,且需是 git 仓库,stage1 靠 git pull 更新)
//   devops kit /srv/kit/ansible,/srv/kit/k8s (ansible + k8s 清单,只读)
//   kubeconfig /var/jenkins_home/.kube/config (连本地 K3d)
//   ssh        /var/jenkins_home/.ssh       (Ansible 经 Tailscale 连云端)
//
// GitHub 触发:Jenkins 任务勾 "Build when a change is pushed to GitHub",
// 本机无公网 IP,用 Tailscale Funnel 暴露 8080 接收 webhook(详见 GITHUB_WEBHOOK_JENKINS_SETUP.md)。
// webhook 只负责触发;流水线自己 git pull 最新代码(见 stage 1)。

pipeline {
  agent any
  environment {
    REGISTRY = '100.82.117.31:5000'
    IMAGE    = 'fastapi-app'
    TAG      = "build-${BUILD_ID}"
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
        // Mac 是 arm,云端是 amd64,必须 --platform linux/amd64
        sh "docker build --platform linux/amd64 -t ${REGISTRY}/${IMAGE}:${TAG} ${APP_DIR}"
        // 同时打 :latest(k3d 测试阶段用,清单里写的是 latest),与部署用的 :TAG 是同一份构建产物
        sh "docker tag ${REGISTRY}/${IMAGE}:${TAG} ${REGISTRY}/${IMAGE}:latest"
        sh "docker push ${REGISTRY}/${IMAGE}:${TAG}"
        sh "docker push ${REGISTRY}/${IMAGE}:latest"
      }
    }

    stage('3 K3d 测试(pytest 冒烟)') {
      steps {
        sh "kubectl create namespace ${TEST_NS} || true"
        // 把 Python 测试脚本做成 ConfigMap 挂进测试 Job(幂等:已有则更新)
        sh "kubectl -n ${TEST_NS} create configmap prepilot-test-script " +
           "--from-file=test_api.py=${KIT_DIR}/k8s/test/test_api.py " +
           "--dry-run=client -o yaml | kubectl -n ${TEST_NS} apply -f -"

        // 关键:先把刚构建的镜像直接导入 k3d 两个节点的 containerd,再 apply,避免 k3d 节点去 registry 拉取。
        // 100.82.117.31 是 Mac 的 Tailscale IP,k3d 节点(在 OrbStack 内)未必能路由到/信任该 insecure
        // registry;本地验证已证明 image import + IfNotPresent 这条路稳。Jenkins 容器有 docker socket,
        // 用 docker save -> docker cp -> ctr import 把镜像塞进各节点。
        // 注意:
        //  1) 必须用节点内【裸 ctr】(containerd CLI)+ 显式 k3s socket(/run/k3s/containerd/containerd.sock)
        //     与 k8s.io 命名空间;【不能】用 "k3s ctr" —— docker exec 下 k3s 子命令分发失败,报
        //     "No help topic for 'ctr'"。
        //  2) 必须加 --all-platforms:构建镜像是 linux/amd64,而 k3d 节点是 arm64(OrbStack/M1),
        //     ctr 默认按节点平台解包会报 "no match for platform in manifest"。--all-platforms 让 ctr
        //     导入归档内全部平台(此处仅 amd64)并据此解包,靠 OrbStack 的 amd64 模拟跑起来。
        //  中途可能出现 "content digest ... not found" 的 ERRO 行属正常(层异步写入),末行
        //  Successfully imported + 退出码 0 即可。
        sh "docker save ${REGISTRY}/${IMAGE}:latest -o /tmp/${IMAGE}-${BUILD_ID}.tar"
        sh "docker cp /tmp/${IMAGE}-${BUILD_ID}.tar k3d-dev-cluster-server-0:/tmp/"
        sh "docker exec k3d-dev-cluster-server-0 ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import --all-platforms /tmp/${IMAGE}-${BUILD_ID}.tar"
        sh "docker cp /tmp/${IMAGE}-${BUILD_ID}.tar k3d-dev-cluster-agent-0:/tmp/"
        sh "docker exec k3d-dev-cluster-agent-0 ctr -a /run/k3s/containerd/containerd.sock -n k8s.io images import --all-platforms /tmp/${IMAGE}-${BUILD_ID}.tar"
        sh "rm -f /tmp/${IMAGE}-${BUILD_ID}.tar || true"

        // 被测服务(Deployment+Service) + 测试 Job 一起 apply(此时镜像已在节点本地)
        sh "kubectl -n ${TEST_NS} apply -f ${KIT_DIR}/k8s/test/"
        // 兜底:确保用本地镜像,不尝试从 registry 拉(app.yaml 已写 IfNotPresent,这里无害)
        sh "kubectl -n ${TEST_NS} patch deployment prepilot-app -p '{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"app\",\"imagePullPolicy\":\"IfNotPresent\"}]}}}}'"

        // 等 Deployment 就绪(镜像已在节点本地)
        sh "kubectl -n ${TEST_NS} rollout status deployment/prepilot-app --timeout=180s"
        // 等测试 Job 跑完(脚本内部已重试等 app 就绪;失败则 Job 非 0 退出 -> 此处超时失败)
        sh "kubectl -n ${TEST_NS} wait --for=condition=complete job/prepilot-test --timeout=300s"
        // 本环境 kubectl logs 偶发 502,失败不影响结论(wait 已证明 pytest 通过);用 || true 防误判
        sh "kubectl -n ${TEST_NS} logs job/prepilot-test || true"
      }
    }

    stage('4 Ansible 部署云端') {
      steps {
        // 走 Tailscale 内网 100.116.132.10,用挂载的 SSH key 驱动 docker compose pull && up -d
        sh "ansible-playbook -i ${KIT_DIR}/ansible/inventory/hosts.yml " +
           "${KIT_DIR}/ansible/playbooks/deploy-prepilot.yml -e image_tag=${TAG}"
      }
    }

    stage('5 云端公网冒烟') {
      steps {
        // 经 nginx(80)走完整链路:公网 /api/health -> nginx -> api:8000/health
        sh "curl -f http://182.254.244.139/api/health"
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
