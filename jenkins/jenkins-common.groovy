/**
 * Shared helpers for UBR Jenkins pipelines (GUI, Regression, Throughput).
 * Load in each Jenkinsfile: def senao = load 'jenkins/jenkins-common.groovy'
 */

def resolveUserEmail() {
    def buildCauses = currentBuild.getBuildCauses()
    def userCause = buildCauses.find { it._class == 'hudson.model.Cause$UserIdCause' }
    def buildUserId = userCause?.userId ?: 'unknown'
    def userEmails = [
        'puneet': 'puneet.sharma@senao.com',
        'harman': 'harmanjot.singh@senao.com',
        'mahesh': 'mahesh.battala@senao.com',
        'sandeep': 'sandeep.manchikanti@senao.com',
        'sampath': 'sampath.marella@senao.com',
        'jayanth': 'jayanth.munnaluru@senao.com',
        'srilatha': 'srilatha.tadiboina@senao.com',
        'phani': 'phani.darla@senao.com',
        'dinesh': 'dinesh.redddy@senao.com'
    ]
    return [
        name: userCause?.userName ?: 'Unknown User',
        id: buildUserId,
        email: userEmails.get(buildUserId, 'harmanjot.singh@senao.com')
    ]
}

def standardReportBasename(String prefix) {
    def dateStr = new Date().format('yyyyMMdd')
    return "${prefix}_${env.BUILD_ID}_Report_${dateStr}"
}

def copyRegressionReport(String destFile) {
    sh """
        set -e
        src='reports/artifacts/Regression_Report.html'
        if [ ! -f "\$src" ]; then
          latest=\$(ls -t reports/artifacts/Regression_Report_*.html 2>/dev/null | head -1 || true)
          if [ -z "\$latest" ]; then
            echo "No reports/artifacts/Regression_Report.html (or legacy timestamped copy) found"
            exit 1
          fi
          src="\$latest"
        fi
        cp "\$src" "${destFile}"
        echo "Copied \$src -> ${destFile}"
    """
}

def copyLatestPerformanceReport(String destFile) {
    sh """
        set -e
        latest=\$(ls -t reports/artifacts/Performance_Report_*.html 2>/dev/null | head -1 || true)
        if [ -z "\$latest" ]; then
          echo "No reports/artifacts/Performance_Report_*.html found"
          exit 1
        fi
        cp "\$latest" "${destFile}"
        echo "Copied \$latest -> ${destFile}"
    """
}

def publishSenaoHtmlReport(String reportFile, String reportLabel) {
    if (!reportFile || !fileExists(reportFile)) {
        echo "HTML report not found for publishHTML: ${reportFile}"
        return false
    }
    def parts = reportFile.tokenize('/')
    def reportName = parts[-1]
    def reportDir = parts.size() > 1 ? parts[0..-2].join('/') : '.'
    publishHTML(target: [
        allowMissing: false,
        alwaysLinkToLastBuild: true,
        keepAll: true,
        reportDir: reportDir,
        reportFiles: reportName,
        reportName: reportLabel
    ])
    return true
}

def sendSenaoPipelineEmail(Map cfg) {
    def user = resolveUserEmail()
    def mailDate = new Date().format('dd MMM yyyy, HH:mm:ss z', TimeZone.getTimeZone('Asia/Kolkata'))
    def resultColor = currentBuild.currentResult == 'SUCCESS' ? 'green' : 'orange'
    def reportFile = cfg.reportFile ?: ''
    def hasReport = reportFile && fileExists(reportFile)
    def csvFile = cfg.csvFile ?: ''
    def hasCsv = csvFile && fileExists(csvFile)

    def reportSection = hasReport
        ? """<p><strong>HTML report:</strong>
            <a href="${env.BUILD_URL}artifact/${reportFile}">Download report</a>
            &nbsp;|&nbsp;
            <a href="${env.BUILD_URL}">Open build in Jenkins</a></p>"""
        : """<p><strong>HTML report:</strong> not generated.
            See the <a href="${env.BUILD_URL}console">build console</a>.</p>"""

    def attachments = []
    if (hasReport) {
        attachments << reportFile
    }
    if (hasCsv) {
        attachments << csvFile
    }

    def mailParams = [
        subject: "${cfg.subjectPrefix} — ${currentBuild.fullDisplayName} — ${currentBuild.currentResult}",
        body: """\
            <html><body style="font-family:Segoe UI,sans-serif;color:#334155;">
                <h2 style="color:#1e3a8a;">${cfg.reportTitle}</h2>
                <p><strong>Build:</strong> ${currentBuild.fullDisplayName}</p>
                <p><strong>Status:</strong> <span style="color:${resultColor};">${currentBuild.currentResult}</span></p>
                <p><strong>Started by:</strong> ${user.name} (${user.id})</p>
                <p><strong>Time:</strong> ${mailDate}</p>
                <p><strong>Target stand:</strong> ${TARGET_STAND}</p>
                <hr/>
                ${cfg.detailsHtml ?: ''}
                ${reportSection}
                <p style="color:#64748b;font-size:12px;">Senao UBR Automation — ${cfg.footerNote}</p>
            </body></html>
        """,
        to: user.email,
        mimeType: 'text/html'
    ]
    if (!attachments.isEmpty()) {
        mailParams.attachmentsPattern = attachments.join(',')
    }
    emailext(mailParams)
}

return this
