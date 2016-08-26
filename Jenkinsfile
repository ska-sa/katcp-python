node('docker') {
    stage 'Cleanup workspace'
    dir('dist') {
        deleteDir()
    }

    docker.image('cambuilder:latest').inside('-u root') {
        stage 'Checkout SCM'
            checkout scm
            sh "git checkout ${env.BRANCH_NAME}"

        stage 'Install & Unit Tests'
            timeout(time: 30, unit: 'MINUTES') {
	        sh 'pip install . -U --pre --user'
	        sh 'python setup.py nosetests -v --with-xunit'
	        step([$class: 'JUnitResultArchiver', testResults: 'nosetests.xml'])
	        }

        stage 'Build .whl & .deb'
            sh 'fpm -s python -t deb .'
            sh 'python setup.py bdist_wheel'
            sh 'mv *.deb dist/'
            // chmod for cleanup stage
            sh 'chmod 777 -R dist'

        stage 'Archive build artifact: .whl & .deb'
            archive 'dist/*.whl,dist/*.deb'

        stage 'Trigger downstream publish'
            build job: 'publish-local', parameters: [
                string(name: 'artifact_source', value: "${currentBuild.absoluteUrl}/artifact/dist/*zip*/dist.zip"),
                string(name: 'source_branch', value: "${env.BRANCH_NAME}")]
    }
}
